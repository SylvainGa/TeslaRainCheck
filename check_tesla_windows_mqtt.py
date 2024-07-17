#!/usr/bin/env python3

import sys
import requests
import json
import time
import pytz
from suntime import Sun
from threading import Timer
import smtplib
import configparser
import geopy.distance
import paho.mqtt.client as mqtt
from datetime import datetime, timedelta, time
from pprint import pprint

# Class used to send an email 
class Emailer:
    def sendmail(self, recipient, subject, content):
        SMTP_SERVER = 'smtp.gmail.com' #Email Server (don't change!)
        SMTP_PORT = 587 #Server Port (don't change!)
        GMAIL_USERNAME = Config.get('Email', 'username') #change this to match your gmail account
        GMAIL_PASSWORD = Config.get('Email', 'password') #change this to match your gmail password

        #Create Headers
        headers = ["From: " + GMAIL_USERNAME, "Subject: " + subject, "To: " + recipient, "MIME-Version: 1.0", "Content-Type: text/html"]
        headers = "\r\n".join(headers)

        #Connect to Gmail Server
        session = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        session.ehlo()
        session.starttls()
        session.ehlo()

        #Login to Gmail
        session.login(GMAIL_USERNAME, GMAIL_PASSWORD)

        #Send Email & Exit
        session.sendmail(GMAIL_USERNAME, recipient, headers + "\r\n\r\n" + content)
        session.quit

# The MQTT callback for when the client receives a CONNACK response from the server.
def on_mqtt_connect(client, userdata, flags, rc):
    print("Tesla-MQTT: Connected to MQTT with result code "+str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("acurite/loop")

# The MQTT callback for when a PUBLISH message is received from the server.
def on_mqtt_message(client, userdata, msg):
    #print(msg.topic+" "+str(msg.payload.decode('utf-8')))
    jsondata = json.loads(str(msg.payload.decode('utf-8')))
    rain_cm = jsondata.get('rain_cm')
    #rain_cm = "0.01"	# DEBUG To test the code when it rains

    global g_mqtt_raining
    global g_mqtt_lastRun
    global g_mqtt_ran
    global g_out_temp
    global g_own_raining

    if jsondata.get('outTemp_C') is not None:
        g_out_temp = float(jsondata.get('outTemp_C'))

    now = datetime.now()
    g_mqtt_lastRun = now
    g_mqtt_ran = True
    current_time = now.strftime("%H:%M:%S")
    #breakpoint() ############################################## BREAKPOINT ##############################################

    # Read how much rain as fallen
    if rain_cm is not None:
        rain = float(rain_cm)
    else:
        rain = 0.0

    print("Tesla-MQTT: " + current_time + ": " + str(rain) + " cm")
    if rain > 0.0:
        if g_mqtt_raining == False and g_owm_raining == False:    # We'll reset to False once the rain has stopped, so we don't keep pounding the vehicle for the same rain shower
            g_mqtt_raining = True 
            raining_check_windows() # It's raining according to MQTT, let's check our windows (and OWM hasn't seen rain yet)
        else:
            print("Tesla-MQTT: Skipping, waiting for the rain to stop")
    else:
        g_mqtt_raining = False
        #print("Tesla-MQTT: All is fine")

def tessie(command, extra):
    url = "https://api.tessie.com/" + vin + "/" + command + extra
    headers = {
        "accept": "application/json",
        "authorization": "Bearer " + tessie_token
    }

    #print("url=" + url)
    #print(json.dumps(headers, indent = 4))
    response = requests.get(url, headers=headers)
    #print(response.status_code)
    #print(response.json())
    return response

def get_vehicle_status():
    response = tessie("status", "")
    #print(response.status_code)
    #print(response.json())
    if response.status_code == 200:
        status = response.json().get("status")
        return status
    else:
        return str(response.status_code)
        
def raining_check_windows():
    global g_windows
    global g_moving
    global g_longitude
    global g_latitude

    # Get the state of the vehicle first
    vehicle_status = get_vehicle_status()

    # Read data that I need from the vehicle
    response = tessie("state", "")
    if response.status_code != 200:
        if g_already_sent_email_after_error == False:
            g_already_sent_email_after_error = True

            emailBody = "Error #" + response.status_code + " getting vehicle data for VIN " + vin

            sender = Emailer()
            emailSubject = "Tesla-CheckRain: " + emailBody
            sender.sendmail(sendTo, emailSubject, emailBody)

            print(emailSubject)
            print("Tesla-CheckRain: " + emailBody)

        return;
    
    vehicle_state = response.json().get("vehicle_state")
    climate_state = response.json().get("climate_state")
    charge_state  = response.json().get("charge_state")
    drive_state   = response.json().get("drive_state")
    
    if vehicle_state == None or drive_state == None or climate_state == None or charge_state == None:
        if g_already_sent_email_after_error == False:
            g_already_sent_email_after_error = True

            emailBody = "Missing data reading vehicle state for VIN " + vin

            sender = Emailer()
            emailSubject = "Tesla-CheckRain: " + emailBody
            sender.sendmail(sendTo, emailSubject, emailBody)

            print(emailSubject)
            print("Tesla-CheckRain: " + emailBody)

        return;

    g_already_sent_email_after_error = False; 

    g_moving = drive_state['shift_state']

    fd_window = int(vehicle_state['fd_window'])
    fp_window = int(vehicle_state['fp_window'])
    rd_window = int(vehicle_state['rd_window'])
    rp_window = int(vehicle_state['rp_window'])
    g_windows = fd_window + fp_window + rd_window + rp_window # 0 means close so when we add them up, anything but 0 means at least a window is opened

    g_latitude  = drive_state['latitude']
    g_longitude = drive_state['longitude']
    
    if g_latitude == None or g_longitude == None:
        print("Missing Latitude or Longitude, assuming we're at our station")
        latitude = station_latitude
        longitude = station_longitude
    else:
        latitude = g_latitude
        longitude = g_longitude

    # Our windows are opened and we are parked
    if (g_windows is not None and g_windows > 0) and (g_moving is None or g_moving == "P"):
        # Now check if we're close to our station. If not, ignore the rain
        station = (station_latitude, station_longitude)
        vehicle_position = (float(latitude), float(longitude))
        distance = float(geopy.distance.geodesic(station, vehicle_position).km)

        if distance < max_distance:
            # This is where we close our windows
            response = tessie("command/close_windows", "")
            result = response.json().get("result")
            woke = response.json().get("woke")
            if result == True:
                emailBody = "We're parked close enough to our station with our windows opened in the rain! Closing them"
            else:
                emailBody = "It's raining and we're unable to close the windows! Check vehicle!"
        else:
            emailBody = "We're parked with our windows opened in the rain but too far (" + "%.1f" % distance + " km) to be sure it's raining on us, so leaving as is"

        emailSubject = "Tesla-CheckRain: It has rained " + rain_cm + " cm at " + current_time
        sender = Emailer()
        sender.sendmail(sendTo, emailSubject, emailBody)
    
        print(emailSubject)
        print("Tesla-CheckRain: " + emailBody)
    else:
        print("Tesla-CheckRain: It has rained " + rain_cm + " cm at " + current_time + " and our windows are closed or the vehicle is moving")

    return
    
class RepeatTimer(Timer):
    def run(self):
        while not self.finished.wait(self.interval):
            self.function(*self.args, **self.kwargs)

def on_watchdog():
    global g_mqtt_ran
    global g_timer_ran

    now = datetime.now()
    current_time = now.strftime("%H:%M:%S")
    print("Tesla-WD: " + current_time + ": Last mqtt thread ran at " + g_mqtt_lastRun.strftime("%H:%M:%S") + " last timer thread ran at " + g_timer_lastRun.strftime("%H:%M:%S"))

    # If our last mqtt data fetch plus 60 seconds is less than now, the mqtt hasn't received data for too long
    if g_mqtt_lastRun + timedelta(seconds = 60) < now:
        if g_mqtt_ran == True:
            g_mqtt_ran = False

            emailBody = "Last ran at " + g_mqtt_lastRun.strftime("%H:%M:%S")

            sender = Emailer()
            emailSubject = "Tesla-WD: WeeWX - MQTT thread hasn't ran in over a minute, quitting program"
            sender.sendmail(sendTo, emailSubject, emailBody)

            print(emailSubject)

            quit(1) # Quit so systemctl respawn the process because 60 seconds without data from the station isn't normal

    # If our last timer run plus 90 seconds is less than now, the timer hasn't ran for too long
    if g_timer_lastRun + timedelta(seconds = 90) < now:
        if g_timer_ran == True:
            g_timer_ran = False

            emailBody = "Last ran at " + g_timer_lastRun.strftime("%H:%M:%S")

            sender = Emailer()
            emailSubject = "Tesla-WD: WeeWX - Timer thread hasn't ran in over 1.5 minutes, quitting program"
            sender.sendmail(sendTo, emailSubject, emailBody)

            print(emailSubject)

            quit(1) # Quit so systemctl respawn the process because 90 seconds without running the timer isn't normal

def on_timer():
    global g_night
    global g_timer_lastRun
    global g_timer_ran
    global g_out_temp
    global g_windows
    global g_moving
    global g_longitude
    global g_latitude
    global g_owm_raining
    global g_mqtt_raining
    global g_already_sent_email_after_error
    
    now = datetime.now()
    g_timer_lastRun = now
    g_timer_ran = True

    current_time = now.strftime("%H:%M:%S")

    # Get the state of the vehicle first
    vehicle_status = get_vehicle_status()

    # Read data that I need from the vehicle
    response = tessie("state", "")
    if response.status_code != 200:
        if g_already_sent_email_after_error == False:
            g_already_sent_email_after_error = True

            emailBody = "Error #" + response.status_code + " getting vehicle data for VIN " + vin

            sender = Emailer()
            emailSubject = "Tesla-Timer: " + emailBody
            sender.sendmail(sendTo, emailSubject, emailBody)

            print(emailSubject)
            print("Tesla-Timer: " + emailBody)

        return;
    
    vehicle_state = response.json().get("vehicle_state")
    climate_state = response.json().get("climate_state")
    charge_state  = response.json().get("charge_state")
    drive_state   = response.json().get("drive_state")
    
    if vehicle_state == None or drive_state == None or climate_state == None or charge_state == None:
        if g_already_sent_email_after_error == False:
            g_already_sent_email_after_error = True

            emailBody = "Missing data reading vehicle state for VIN " + vin

            sender = Emailer()
            emailSubject = "Tesla-Timer: " + emailBody
            sender.sendmail(sendTo, emailSubject, emailBody)

            print(emailSubject)
            print("Tesla-Timer: " + emailBody)

        return;

    g_already_sent_email_after_error = False; 

    g_moving = drive_state['shift_state']
    if g_moving is not None and g_moving != "P":
        print("Vehicle in motion, skipping checking inside temperature and windows")
        return

    fd_window = int(vehicle_state['fd_window'])
    fp_window = int(vehicle_state['fp_window'])
    rd_window = int(vehicle_state['rd_window'])
    rp_window = int(vehicle_state['rp_window'])
    g_windows = fd_window + fp_window + rd_window + rp_window # 0 means close so when we add them up, anything but 0 means at least a window is opened

    g_latitude  = drive_state['latitude']
    g_longitude = drive_state['longitude']
    
    if g_latitude == None or g_longitude == None:
        print("Missing Latitude or Longitude, assuming we're at our station")
        latitude = station_latitude
        longitude = station_longitude
    else:
        latitude = g_latitude
        longitude = g_longitude
    
    # Check if the sun has set and if our windows are still opened
    sun = Sun(latitude, longitude)
    today_sr = sun.get_sunrise_time()
    today_ss = sun.get_sunset_time()
    if today_sr > today_ss:
        today_ss = today_ss + timedelta(days=1) # Bug in the routine, day isn't update when crossing over midnight in UTC timezone
    tz_toronto = pytz.timezone('America/Toronto')
    now_tz = tz_toronto.localize(now)
    #print("Tesla-Timer: Debug: today_sr: " + str(today_sr))
    #print("Tesla-Timer: Debug: today_ss: " + str(today_ss))
    #print("Tesla-Timer: Debug: now_tz: " + str(now_tz))
    if today_sr > now_tz or now_tz > today_ss:
        if g_night == False:  # First time going in since the sun has set, check if we're parked with the windows down and if so, close them
            g_night = True
            print("Tesla-Timer: It's night, check if our windows are closed")
            if g_windows is not None and g_windows > 0:
                response = tessie("command/close_windows", "")
                result = response.json().get("result")
                woke = response.json().get("woke")
                if result == True:
                    emailBody = "Closing windows because it's night time."

                    emailSubject = "Tesla-Timer: Windows were opened at sunset (" + current_time + ")"
                else:
                    emailBody = "Unable to close windows at sunset. Check vehicle!"

                    emailSubject = "Tesla-Timer: " + emailBody

                sender = Emailer()
                sender.sendmail(sendTo, emailSubject, emailBody)

                print(emailSubject)
                print("Tesla-Timer: " + emailBody + "woke is " + woke)
                
            else:
                print("Our windows are closed")
    else:
        g_night = False
        print("Tesla-Timer: Debug: Still daytime with diff of " + str(today_ss - now_tz))

        # Check if it's during the hottest part of the day, it's warm outside and the sun might be out - All conditions to have a warm inside. If set, keep the vehicle awake so cabin protection can do its stuff
        if owm_key is not None:
            URL = "https://api.openweathermap.org/data/2.5/weather?lat=" + str(latitude) + "&lon=" + str(longitude) + "&appid=" + str(owm_key)
            response = requests.get(URL)
            if response.status_code == 200:
                print(json.dumps(response.json(), indent = 4))
                g_owm_raining = False # We assume it's not raining
                data = response.json()
                icon = data['weather'][0]['icon']
                if int(icon[0:2]) < 4: # Icon with a number lower than 4 means there is some sun showing
                    if today_sr + timedelta(hours=3) < now_tz < today_ss - timedelta(hours=3): # Sun is up high enough in the sky
                        # Favor the car temperature
                        if vehicle_status == "awake":
                            g_out_temp = climate_state['outside_temp']
                        else:
                            g_out_temp = float(data['main']['temp']) - 273.15

                        if g_out_temp is not None and g_out_temp > 10.0: # Below 10C means it's not hot enough to overheat the cabin
                            # Before we go any further, we must make sure the battery level is at least 20% to prevent running down the battery too much
                            #breakpoint() ############################################## BREAKPOINT ##############################################
                            soc = charge_state['battery_level']
                            if soc is not None and soc >= 20:
                                #vehicles[vehicle].sync_wake_up()  # Keep the vehicle awake so cabin overheat protection can do its stuff if needed <- Only works for 12 hours after a drive, not when awaken :-(
                                if vehicle_status == "awake":
                                    print("Climate is " + json.dumps(headers, indent = 4))
                                    inside_temp = climate_state['inside_temp']
                                    active_cooling =  climate_state['cabin_overheat_protection_actively_cooling']
                                    print("Fan is running: " + str(active_cooling))
                                    print("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") during mid-day and outside is warm at " + str(g_out_temp) + "C with inside at " + str(inside_temp) + "C - The vehicle is awake!")
                                else:
                                    print("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") during mid-day and outside is warm at " + str(g_out_temp) + "C - Vehicle is asleep so can't get its inside temperature")
                            else:
                                print("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") during mid-day and outside is warm at " + str(g_out_temp) + "C but not waking the vehicle because SoC at " + str(soc) + "%")
                        else:
                            print("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") during mid-day and outside is cold at " + str(g_out_temp) + "C")
                    else:
                        print("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") but too early or late to be warm enough")
                else:
                    if int(icon[0:2]) >= 9 and int(icon[0:2]) <= 11:
                        if g_mqtt_raining == False and g_owm_raining == False: # We'll reset to False once the rain has stopped, so we don't keep pounding the vehicle for the same rain shower
                            g_owm_raining = True # It's raining according to OWM, let's check our windows (and MQTT hasn't seen rain yet)
                            raining_check_windows()
                        else:
                            print("Tesla-Timer: Skipping, waiting for the rain to stop")

                    print("Tesla-Timer: Debug: Sun shouldn't be visible with " + data['weather'][0]['description'] + " (" + str(icon) + ")")
            else:
                print("Tesla-Timer: Debug: OWN returned " + str(response.status_code))
        else:
            print("Tesla-Timer: Debug: No OWM token")

####### Start here

# Read our config
Config = configparser.ConfigParser()
Config.read("check_tesla_windows_mqtt.ini")

# Initialise our global variables
tesla = None
vin = Config.get('Tesla', 'vin')
tessie_token = Config.get('Tesla', 'tessie_token')
wake_at_start = int(Config.get('Tesla', 'wake_at_start'))
sendTo = Config.get('Email', 'to')
station_latitude = float(Config.get('MQTT', 'latitude'))
station_longitude = float(Config.get('MQTT', 'longitude'))

max_distance = float(Config.get('MQTT', 'max_distance'))
owm_key = Config.get('OWM', 'api_key')

g_mqtt_lastRun = datetime.now()
g_mqtt_ran = True
g_timer_lastRun = datetime.now()
g_timer_ran = True
g_night = False
g_out_temp = None
g_already_sent_email_after_error = False

# These are our Tesla data we need to keep while we're running
g_windows = None
g_moving = None
g_latitude = None
g_longitude = None
g_mqtt_raining = False
g_owm_raining = False

# Set up our MQTT connection
client = mqtt.Client()
client.on_connect = on_mqtt_connect
client.on_message = on_mqtt_message

if Config.getboolean('MQTT', 'use_tls') == True:
    client.tls_set()
client.username_pw_set(username = Config.get('MQTT', 'username'), password = Config.get('MQTT', 'password'))

print("Tesla: Connecting to MQTT...")
client.connect(Config.get('MQTT', 'hostname'), int(Config.get('MQTT', 'port')), 60)

print("Tesla: Checking vehicle's status")
vehicle_status = get_vehicle_status()
if (vehicle_status == "asleep" or vehicle_status == "waiting_for_sleep"):
    if wake_at_start == 1:
        print("Waking up vehicle " + vin)
        response = tessie("wake", "")
    else:
        print("Vehicle " + vin + " is asleep and we're not requesting it to be waken up")
elif status == "awake":
    print("Vehicle " + vin + " is already awake")
else:
    print("Vehicle " + vin + " returned a status of " + vehicle_status)

print("Running first instace of the timer thread")
on_timer()

print("Tesla: Starting timer thread with an interval of 60 seconds")
T = RepeatTimer(60, on_timer)
T.start()

print("Tesla: Starting watchdog thread with an interval of 90 seconds")
W = RepeatTimer(90, on_watchdog)
W.start()

# Blocking call that processes network traffic, dispatches callbacks and
# handles reconnecting.
# Other loop*() functions are available that give a threaded interface and a
# manual interface.
client.loop_forever()
