#!/usr/bin/env python3

import sys
sys.path.insert(0, './TeslaPy')

import teslapy
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

    #rain_cm = "0.01"	# To test the code when it rains

    global raining
    global mqtt_lastRun
    global mqtt_ran
    global out_temp
 
    if jsondata.get('outTemp_C') is not None:
        out_temp = float(jsondata.get('outTemp_C'))

    now = datetime.now()
    mqtt_lastRun = now
    mqtt_ran = True
    current_time = now.strftime("%H:%M:%S")

    # Read how much rain as fallen
    if rain_cm is not None:
        rain = float(rain_cm)
    else:
        rain = 0.0

    print("Tesla-MQTT: " + current_time + ": " + str(rain) + " cm")
    if rain > 0.0:
        if raining == False:    # We'll reset to False once the rain has stopped, so we don't keep pounding the vehicle for the same rain shower
            raining = True

            authenticateVehicle(tesla)
            vehicles = tesla.vehicle_list()
            vehicleData = vehicles[vehicle].get_latest_vehicle_data()
            moving = vehicleData['drive_state']['shift_state']
            fd_window = int(vehicleData['vehicle_state']['fd_window'])
            fp_window = int(vehicleData['vehicle_state']['fp_window'])
            rd_window = int(vehicleData['vehicle_state']['rd_window'])
            rp_window = int(vehicleData['vehicle_state']['rp_window'])
            windows = fd_window + fp_window + rd_window + rp_window # 0 means close so when we add them up, anything but 0 means at least a window is opened
            if moving is None and windows > 0: # vehicle is parked with some windows opened
                latitude = vehicleData['drive_state']['latitude']
                longitude = vehicleData['drive_state']['longitude']

                # Now check if we're close to our station. If not, ignore the rain
                station = (station_latitude, station_longitude)
                vehicle_position = (float(latitude), float(longitude))
                distance = float(geopy.distance.geodesic(station, vehicle_position).km)
                if distance < max_distance:
                    vehicles[vehicle].sync_wake_up()  # We need wake up the vehicle to close its windows
                    vehicles[vehicle].command('WINDOW_CONTROL', command='close', lat=latitude, lon=longitude)
                    emailBody = "We're parked close enough to our station with our windows opened in the rain! Closing them"
                else:
                    emailBody = "We're parked with our windows opened in the rain but too far (" + "%.1f" % distance + " km) to be sure it's raining on us, so leaving as is"

                emailSubject = "Tesla-MQTT: WeeWX - It has rained " + rain_cm + " cm at " + current_time
                sender = Emailer()
                sender.sendmail(sendTo, emailSubject, emailBody)
            
                print(emailSubject)
                print("Tesla-MQTT: " + emailBody)
            else:
                print("Tesla-MQTT: WeeWX - It has rained " + rain_cm + " cm at " + current_time + " and our windows are closed or the vehicle is moving")
        else:
            print("Tesla-MQTT: Skipping, waiting for the rain to stop")
    else:
        raining = False
        #print("Tesla-MQTT: All is fine")

class RepeatTimer(Timer):
    def run(self):
        while not self.finished.wait(self.interval):
            self.function(*self.args, **self.kwargs)

def on_watchdog():
    global mqtt_ran
    global timer_ran

    now = datetime.now()
    current_time = now.strftime("%H:%M:%S")
    print("Tesla-WD: " + current_time + ": Last mqtt thread ran at " + mqtt_lastRun.strftime("%H:%M:%S") + " last timer thread ran at " + timer_lastRun.strftime("%H:%M:%S"))

    # If our last mqtt data fetch plus 60 seconds is less than now, the mqtt hasn't received data for too long
    if mqtt_lastRun + timedelta(seconds = 60) < now:
        if mqtt_ran == True:
            mqtt_ran = False

            emailBody = "Last ran at " + mqtt_lastRun.strftime("%H:%M:%S")

            sender = Emailer()
            emailSubject = "Tesla-WD: WeeWX - MQTT thread hasn't ran in over a minute, quitting program"
            sender.sendmail(sendTo, emailSubject, emailBody)

            print(emailSubject)

            quit(1) # Quit so systemctl respawn the process because 60 seconds without data from the station isn't normal

    # If our last timer run plus 90 seconds is less than now, the timer hasn't ran for too long
    if timer_lastRun + timedelta(seconds = 90) < now:
        if timer_ran == True:
            timer_ran = False

            emailBody = "Last ran at " + timer_lastRun.strftime("%H:%M:%S")

            sender = Emailer()
            emailSubject = "Tesla-WD: WeeWX - Timer thread hasn't ran in over 1.5 minutes, quitting program"
            sender.sendmail(sendTo, emailSubject, emailBody)

            print(emailSubject)

            quit(1) # Quit so systemctl respawn the process because 90 seconds without running the timer isn't normal

def on_timer():
    global night
    global timer_lastRun
    global timer_ran
    global awaken_since
    
    now = datetime.now()
    timer_lastRun = now
    timer_ran = True

    current_time = now.strftime("%H:%M:%S")

    # Read data that I need from the vehicle
    authenticateVehicle(tesla)
    vehicles = tesla.vehicle_list()
    vehicleData = vehicles[vehicle].get_latest_vehicle_data()
    moving = vehicleData['drive_state']['shift_state']

    if moving is not None:
        print("Tesla-Timer: Debug: Vehicle in motion, skipping checking inside temperature and windows")
        return

    fd_window = int(vehicleData['vehicle_state']['fd_window'])
    fp_window = int(vehicleData['vehicle_state']['fp_window'])
    rd_window = int(vehicleData['vehicle_state']['rd_window'])
    rp_window = int(vehicleData['vehicle_state']['rp_window'])
    windows = fd_window + fp_window + rd_window + rp_window # 0 means close so when we add them up, anything but 0 means at least a window is opened
    latitude = vehicleData['drive_state']['latitude']
    longitude = vehicleData['drive_state']['longitude']
    
    # Check if the sun has set and if our windows are still opened
    sun = Sun(latitude, longitude)
    today_sr = sun.get_sunrise_time()
    today_ss = sun.get_sunset_time()
    if today_sr > today_ss:
        today_ss = today_ss + timedelta(days=1) # Bug in the routine, day isn't update when crossing over midnight in UTC timezone
    tz_toronto = pytz.timezone('America/Toronto')
    now_tz = tz_toronto.localize(now)
    print("Tesla-Timer: Debug: today_sr: " + str(today_sr))
    print("Tesla-Timer: Debug: today_ss: " + str(today_ss))
    print("Tesla-Timer: Debug: now_tz: " + str(now_tz))
    if now_tz > today_ss:
        print("Tesla-Timer: Debug: It's night, check if our windows are closed")
        if night == False:  # First time going in since the sun has set, check if we're parked with the windows down and if so, close them
            if moving is None:
                night = True
                if  windows > 0:
                    vehicles[vehicle].sync_wake_up()  # We need wake up the vehicle to close its windows
                    vehicles[vehicle].command('WINDOW_CONTROL', command='close', lat=latitude, lon=longitude)
                    emailBody = "Closing windows because it's night time."

                    sender = Emailer()
                    emailSubject = "Tesla-Timer: WeeWX - Windows were opened at sunset (" + current_time + ")"
                    sender.sendmail(sendTo, emailSubject, emailBody)

                    print(emailSubject)
                    print("Tesla-Timer: " + emailBody)
    else:
        night = False
        print("Tesla-Timer: Debug: Still daytime with diff of " + str(today_ss - now_tz))

    # Check if it's during the hottest part of the day, it's warm outside and the sun might be out - All conditions to have a warm inside. If set, keep the vehicle awake so cabin protection can do its stuff
    if owm_key is not None:
        URL = "https://api.openweathermap.org/data/2.5/weather?lat=" + str(latitude) + "&lon=" + str(longitude) + "&appid=" + str(owm_key)
        response = requests.get(URL)
        if response.status_code == 200:
            data = response.json()
            icon = data['weather'][0]['icon']
            if int(icon[0:2]) < 4: # Icon with a number lower than 4 means there is some sun showing
                if today_sr + timedelta(hours=3) < now_tz < today_ss - timedelta(hours=3): # Sun is up high enough in the sky
                    if out_temp > 10.0: # Below 10C means it's not hot enough to overheat the cabin
                        # Before we go any further, we must make sure the battery level is at least 20% to prevent running down the battery too much
                        soc = vehicleData['charge_state']['battery_level']
                        if soc >= 20:
                            if vehicles[vehicle]['state'] == 'asleep' or awaken_since + timedelta(minutes = 5) < now: # Check if we need to wake the vehicle
                                print("Tesla-Timer: Debug: Wake the vehicle")
                                vehicles[vehicle].sync_wake_up()  # Keep the vehicle awake so cabin overheat protection can do its stuff if needed <- Only works for 12 hours after a drive, not when awaken :-(
                                vehicleData = vehicles[vehicle].get_vehicle_data()
                                awaken_since = now
                                
                            if vehicles[vehicle]['state'] != 'asleep':
                                vehicleData = vehicles[vehicle].get_vehicle_data()
                                inside_temp = vehicleData['climate_state']['inside_temp']
                                climate = vehicleData['climate_state']
                                print("Tesla-Timer: Debug: Climate is " + str(climate))
                                active_cooling =  vehicleData['climate_state']['cabin_overheat_protection_actively_cooling']
                                print("Tesla-Timer: Debug: Fan is running: " + str(active_cooling))
                                print("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") during mid-day and outside is warm at " + str(out_temp) + "C with inside at " + str(inside_temp) + "C - The vehicle is awake!")
                            else:
                                print("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") during mid-day and outside is warm at " + str(out_temp) + "C - Vehicle is asleep so can't get its inside temperature")
                        else:
                            print("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") during mid-day and outside is warm at " + str(out_temp) + "C but not waking the vehicle because SoC at " + str(soc) + "%")
                    else:
                        print("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") during mid-day and outside is cold at " + str(out_temp) + "C")
                else:
                    print("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") but too early or late to be warm enough")
            else:
                if int(icon[0:2]) >= 9 and int(icon[0:2]) <= 50:
                    owm_raining = data['weather'][0]['description']
                print("Tesla-Timer: Debug: Sun shouldn't be visible with " + data['weather'][0]['description'] + " (" + str(icon) + ")")
        else:
            print("Tesla-Timer: Debug: OWN returned " + str(response.status_code))
    else:
        print("Tesla-Timer: Debug: Not OWM key")

def authenticateVehicle(tesla):
    if not tesla.authorized:
        expires_at = tesla.token['expires_at']
        if expires_at is None:
            emailSubject = "Tesla: WeeWX - First time asking for an access token"
        else:
            expired_at =  datetime.fromtimestamp(expires_at)
            emailSubject = "Tesla: WeeWX - Access token expired at " + expired_at.strftime('%Y-%m-%d %H:%M:%S')
        emailBody = "Tesla WeeWX - My expired token is currently '" + str(tesla.token['refresh_token']) + "'"
        sender = Emailer()
        sender.sendmail(sendTo, emailSubject, emailBody)
        print(emailSubject)

        tesla.refresh_token(refresh_token = Config.get('Tesla', 'refresh_token'))

####### Start here

# Read our config
Config = configparser.ConfigParser()
Config.read("check_tesla_windows_mqtt.ini")

# Initialise our global variables
raining = False
tesla = None
vehicle = int(Config.get('Tesla', 'vehicle'))
sendTo = Config.get('Email', 'to')
station_latitude = float(Config.get('MQTT', 'latitude'))
station_longitude = float(Config.get('MQTT', 'longitude'))

max_distance = float(Config.get('MQTT', 'max_distance'))
owm_key = Config.get('OWM', 'api_key')

mqtt_lastRun = datetime.now()
mqtt_ran = True
timer_lastRun = datetime.now()
timer_ran = True
night = False
out_temp = 0
awaken_since = 0

# Set up our MQTT connection
client = mqtt.Client()
client.on_connect = on_mqtt_connect
client.on_message = on_mqtt_message

if Config.getboolean('MQTT', 'use_tls') == True:
    client.tls_set()
client.username_pw_set(username = Config.get('MQTT', 'username'), password = Config.get('MQTT', 'password'))

print("Tesla: Connecting to MQTT...")
client.connect(Config.get('MQTT', 'hostname'), int(Config.get('MQTT', 'port')), 60)

print("Tesla: Connecting to Tesla servers...")
retry = teslapy.Retry(total=5, status_forcelist=(408, 500, 502, 503, 504, 540))
tesla = teslapy.Tesla(Config.get('Tesla', 'username'), retry=retry, timeout=10)
if tesla is None:
    print("Tesla: Error connecting to Tesla servers")
else:
    print("Tesla: Connected to Tesla servers")

print("Tesla: Starting timer and watchdog thread with interval of 60 and 90 seconds respectively")
T = RepeatTimer(60, on_timer)
T.start()

W = RepeatTimer(60, on_watchdog)
W.start()


# Blocking call that processes network traffic, dispatches callbacks and
# handles reconnecting.
# Other loop*() functions are available that give a threaded interface and a
# manual interface.
client.loop_forever()
