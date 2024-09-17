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
from datetime import datetime, timedelta
import asyncio 

# DEBUG flag
# 0 No debug
# 1 Generic debug
# 2 Verbose debug
# 3 Same as 2 but log a line even in no rain is detected by MQTT

#      12345678 12345678 12345678
# Lvl  XX (0-3)
# MQTT   XXX (4,8,0x10) - map 0x1C
# Tessie    XXX (0x20,0x40,0x80) - map 0xE0
# OWM           XXX (0x100,0x200,0x400) map 0x700 
# Car climate      XXX (0x800,0x1000,0x2000) map 0x3800
# Time                XX X (0x4000, 0x8000, 0x10000) map 0x1C000
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

def printWithTime(text):
    now = datetime.now()
    current_time = now.strftime("%H:%M:%S")
    print(current_time + " : " + text)

# The MQTT callback for when the client receives a CONNACK response from the server.
def on_mqtt_connect(client, userdata, flags, rc):
    printWithTime("Tesla-MQTT: Connected to MQTT with result code " + str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("acurite/loop")

# The MQTT callback for when a PUBLISH message is received from the server.
def on_mqtt_message(client, userdata, msg):
    global g_mqtt_raining
    global g_mqtt_lastRun
    global g_mqtt_ran
    global g_out_temp
    global g_own_raining
    global g_kill_prog
    
    g_mqtt_lastRun = datetime.now()
    g_mqtt_ran = True

    if g_kill_prog == True:
        printWithTime("Tesla-MQTT: Asked to quit")
        quit(1) # Quit so systemctl respawn the process because we were asked to quit. Not elegant but does the work


    if g_debug & 0x10:
        printWithTime(msg.topic + " " + str(msg.payload.decode('utf-8')))

    jsondata = json.loads(str(msg.payload.decode('utf-8')))
    rain_cm = jsondata.get('rain_cm')
    
    if g_debug & 8: # Force rain
        rain_cm = "0.01"	# DEBUG To test the code when it rains

    if "outTemp_C" in jsondata:
        g_out_temp = float(jsondata.get('outTemp_C'))

    # Read how much rain as fallen
    if rain_cm is not None:
        rain = float(rain_cm)
    else:
        rain = 0.0

    if (g_debug & 3) > 2:
        printWithTime("Tesla-MQTT: Debug: {:.4f}".format(rain) + " cm")
    elif (g_debug & 3) > 1 and rain > 0.0:
        printWithTime("Tesla-MQTT: Debug: {:.4f}".format(rain) + " cm")

    if rain > 0.0:
        if g_mqtt_raining == False and g_owm_raining == False:    # We'll reset to False once the rain has stopped, so we don't keep pounding the vehicle for the same rain shower
            g_mqtt_raining = True 
            if (g_debug & 3) > 1:
                printWithTime("Tesla-MQTT: Debug: Calling raining_check_windows")
            raining_check_windows(rain, "") # It's raining according to MQTT, let's check our windows (and OWM hasn't seen rain yet)
            if (g_debug & 3) > 1:
                printWithTime("Tesla-MQTT: Debug: Returning from raining_check_windows")
        else:
            if (g_debug & 3) > 0:
                printWithTime("Tesla-MQTT: Skipping, waiting for the rain to stop")
    else:
        g_mqtt_raining = False
        if g_debug & 4:
            printWithTime("Tesla-MQTT Debug: All is fine")

def tessie(command, extra, timeout):
    global g_t_sec
    global g_timeout_count
    global g_kill_prog
    
    url = "https://api.tessie.com/" + vin + "/" + command + extra
    headers = {
        "accept": "application/json",
        "authorization": "Bearer " + tessie_token
    }

    if g_debug & 0x20:
        printWithTime("url=" + url)
        printWithTime(json.dumps(headers, indent = 4))

    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except Exception as error:
        # if we get three errors in a row, send an email
        g_timeout_count += 1
        if g_timeout_count == 3:
            g_timeout_count = 0

            emailBody = "Tesla-Tessie: command failed with exception: " + str(error)
            emailSubject = "Tesla-Tessie: command failed with exception: " + type(error).__name__

            try:
                sender = Emailer()
                sender.sendmail(sendTo, emailSubject, emailBody)
            except Exception as error:
                # Something is really wrong if this fails too so quit
                if (g_debug & 3) > 0:
                    printWithTime("Tesla-Tessie: Unable to send email because of exception: " + type(error).__name__ + ")")
                g_kill_prog = True
                quit(1) # Quit so systemctl respawn the process because we were asked to quit. Not elegant but does the work

        if (g_debug & 3) > 0:
            printWithTime("Tesla-Tessie: Command failed with exception: " + str(error))

        response = requests.Response() # Build a new Response dict
        response.status_code = -300        
    else:
        g_timeout_count = 0
    
    if g_debug & 0x40:
        printWithTime(response.status_code)
        printWithTime(response.json())

    return response

def get_vehicle_status():
    response = tessie("status", "", g_t_sec)
    if g_debug & 0x80:
        printWithTime(response.status_code)
        printWithTime(response.json())
    if response.status_code == 200:
        status = response.json().get("status")
        return status
    else:
        return str(response.status_code)
        
def raining_check_windows(rain, owm_station):
    global g_windows
    global g_moving
    global g_longitude
    global g_latitude
    global g_wd_timer
    
    # Get the state of the vehicle first
    vehicle_status = get_vehicle_status()

    # Read data that I need from the vehicle
    response = tessie("state", "?use_cache=true", g_t_sec)
    if response.status_code != 200 and response.status_code != -300:
        if g_already_sent_email_after_error == False:
            g_already_sent_email_after_error = True

            emailBody = "Error #" + str(response.status_code) + " getting vehicle data for VIN " + vin
            emailSubject = "Tesla-CheckRain: " + emailBody

            try:
                sender = Emailer()
                sender.sendmail(sendTo, emailSubject, emailBody)
            except Exception as error:
                if (g_debug & 3) > 0:
                    if rain < 0.0:
                        printWithTime("Tesla-Timer: Unable to send email because of exception: " + type(error).__name__ + ")")
                    else:
                        printWithTime("Tesla-MQTT: Unable to send email because of exception: " + type(error).__name__ + ")")

            printWithTime("Tesla-CheckRain: " + emailBody)

        return;
    
    vehicle_state = response.json().get("vehicle_state")
    climate_state = response.json().get("climate_state")
    charge_state  = response.json().get("charge_state")
    drive_state   = response.json().get("drive_state")
    
    if vehicle_state == None or drive_state == None or climate_state == None or charge_state == None:
        if g_already_sent_email_after_error == False:
            g_already_sent_email_after_error = True

            emailBody = "Missing data reading vehicle state for VIN " + vin
            emailSubject = "Tesla-CheckRain: " + emailBody

            try:
                sender = Emailer()
                sender.sendmail(sendTo, emailSubject, emailBody)
            except Exception as error:
                if (g_debug & 3) > 0:
                    printWithTime("Tesla-CheckRain: Unable to send email because of exception: " + type(error).__name__ + ")")

            printWithTime("Tesla-CheckRain: " + emailBody)

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
        if (g_debug & 3) > 2:
            printWithTime("Tesla-CheckRain: Debug: Missing Latitude or Longitude, assuming we're at our station")
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

        if distance < max_distance or rain < 0.0: # If we're close to our station or OWM has seen rain (uses the vehicle's location), close the windows
            # This is where we close our windows
            waitTime = g_wd_timer - 5
            if waitTime > 90:
                waitTime = 90
            response = tessie("command/close_windows", "?retry_duration=" + str(waitTime), waitTime)
            status_code = response.status_code
            if status_code == 200:
                result = response.json().get("result")
                woke = response.json().get("woke")
                if result == True:
                    if rain < 0.0:
                        emailBody = "Our windows are opened and it's raining according to the closest OWM station (" + owm_station + ")! Closing them"
                    else:
                        emailBody = "We're parked close enough to our station with our windows opened in the rain! Closing them"
                else:
                    emailBody = "It's raining and we're unable to close the windows! Check vehicle!"
            else:
                emailBody = "It's raining and we're unable to close the windows! Status code was " + str(status_code) + " Check vehicle!"

                if rain < 0.0:
                    emailSubject = "Tesla-Timer: " + emailBody
                else:
                    emailSubject = "Tesla-MQTT: " + emailBody
        else:
            emailBody = "We're parked with our windows opened in the rain but too far (" + "%.1f" % distance + " km) to be sure it's raining on us, so leaving as is"

        now = datetime.now()
        current_time = now.strftime("%H:%M:%S")
        if rain < 0.0:
            emailSubject = "Tesla-CheckRain: It has rained according to OWM station '" + owm_station + "' at " + current_time
        else:
            emailSubject = "Tesla-CheckRain: It has rained " + str(rain) + " cm at " + current_time

        try:
            sender = Emailer()
            sender.sendmail(sendTo, emailSubject, emailBody)
        except Exception as error:
            if (g_debug & 3) > 0:
                printWithTime("Tesla-CheckRain: Unable to send email because of exception: " + type(error).__name__ + ")")
    
        printWithTime(emailSubject)
        printWithTime("Tesla-CheckRain: " + emailBody)
    else:
        if rain < 0.0:
            if (g_debug & 3) > 0:
                if (g_moving is None or g_moving == "P"):
                    printWithTime("Tesla-CheckRain: It has rained according to OWM and our windows are closed")
                else:
                    printWithTime("Tesla-CheckRain: It has rained according to OWM but the vehicle is moving")
        else:
            if (g_debug & 3) > 0:
                if (g_moving is None or g_moving == "P"):
                    printWithTime("Tesla-CheckRain: It has rained " + str(rain) + " cm and our windows are closed")
                else:
                    printWithTime("Tesla-CheckRain: It has rained " + str(rain) + " cm but the vehicle is moving")

    return
    
class RepeatTimer(Timer):
    global g_kill_prog

    def run(self):
        while not self.finished.wait(self.interval):
            if g_kill_prog == True:
                printWithTime("Tesla-RepeatTimer: Asked to quit")
                quit(1) # Quit so systemctl respawn the process because we were asked to quit. Not elegant but does the work

            self.function(*self.args, **self.kwargs)

def on_watchdog():
    global g_skip_mqtt
    global g_mqtt_ran
    global g_timer_ran
    global g_kill_prog
    global g_wd_timer

    now = datetime.now()
    if g_skip_mqtt:
        if (g_debug & 3) > 0:
            printWithTime("Tesla-WD: last timer thread ran at " + g_timer_lastRun.strftime("%H:%M:%S"))
    else:
        if (g_debug & 3) > 0:
            printWithTime("Tesla-WD: Last mqtt thread ran at " + g_mqtt_lastRun.strftime("%H:%M:%S") + " last timer thread ran at " + g_timer_lastRun.strftime("%H:%M:%S"))

        # If our last mqtt data fetch plus g_wd_mqtt_max seconds is less than now, the mqtt hasn't received data for too long
        if g_mqtt_lastRun + timedelta(seconds = g_wd_mqtt_max) < now:
            if g_mqtt_ran == True:
                g_mqtt_ran = False

                emailBody = "Last ran at " + g_mqtt_lastRun.strftime("%H:%M:%S")
                emailSubject = "Tesla-WD: MQTT thread hasn't ran in over a minute, quitting program"

                try:
                    sender = Emailer()
                    sender.sendmail(sendTo, emailSubject, emailBody)
                except Exception as error:
                    if (g_debug & 3) > 0:
                        printWithTime("Tesla-WD: Unable to send email because of exception: " + type(error).__name__ + ")")

                printWithTime(emailSubject)

                quit(1) # Quit so systemctl respawn the process because 60 seconds without data from the station isn't normal. Not elegant but does the work

    # If our last timer run plus in the time it takes to run the wd timer is less than now, the timer hasn't ran for too long
    if g_timer_lastRun + timedelta(seconds = g_wd_timer) < now:
        if g_timer_ran == True:
            g_timer_ran = False

            emailBody = "Last ran at " + g_timer_lastRun.strftime("%H:%M:%S")
            emailSubject = "Tesla-WD: Timer thread hasn't ran in over " + str(g_wd_timer) + " secondes, quitting program"

            try:
                sender = Emailer()
                sender.sendmail(sendTo, emailSubject, emailBody)
            except Exception as error:
                if (g_debug & 3) > 0:
                    printWithTime("Tesla-WD: Unable to send email because of exception: " + type(error).__name__ + ")")

            printWithTime(emailSubject)
            printWithTime("Tesla-WD: " + emailBody)

            g_kill_prog = True
            
            quit(1) # Quit so systemctl respawn the process because 90 seconds without running the timer isn't normal. Not elegant but does the work

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
    global g_kill_prog
    global g_retry
    global g_debug
    global g_in_timer
    global g_wd_timer
    
    printWithTime("Tesla-Timer: ****************************************************")
    
    now = datetime.now()
    g_timer_lastRun = now
    g_timer_ran = True
    
    if (g_debug & 3) > 1:
        printWithTime("Tesla-Timer: Debug: Setting g_timer_lastRun to " + g_timer_lastRun.strftime("%H:%M:%S"))

    g_in_timer += 1
    if g_in_timer == 2:
        printWithTime("Tesla-Timer: Timer thread already running, skipping this iteration")
        return
    
    if g_in_timer == 3:
        g_kill_prog = True;

        if g_already_sent_email_after_error == False:
            g_already_sent_email_after_error = True

            emailBody = "Timer thread already running FOR TWO ITERARIONS! We must be hung, quiting"
            emailSubject = "Tesla-Timer: " + emailBody

            try:
                sender = Emailer()
                sender.sendmail(sendTo, emailSubject, emailBody)
            except Exception as error:
                printWithTime("Tesla-Timer: Unable to send email because of exception: " + type(error).__name__ + ")")

            printWithTime("Tesla-Timer: " + emailBody)

            quit(1) # Quit so systemctl respawn the process because we were asked to quit. Not elegant but does the work

        return
    
    if g_kill_prog == True:
        printWithTime("Tesla-Timer: Asked to quit")
        quit(1) # Quit so systemctl respawn the process because we were asked to quit. Not elegant but does the work
    
    # Get the state of the vehicle first
    printWithTime("Tesla-Timer: Timer Hang Debug: Querying Tessie Status")
    vehicle_status = get_vehicle_status()

    # Read data that I need from the vehicle
    printWithTime("Tesla-Timer: Timer Hang Debug: Querying Tessie State")
    
    response = tessie("state", "?use_cache=true", g_t_sec)
    if response.status_code != 200 and response.status_code != -300:
        if g_already_sent_email_after_error == False:
            g_already_sent_email_after_error = True

            emailBody = "Error #" + str(response.status_code) + " getting vehicle data for VIN " + vin
            emailSubject = "Tesla-Timer: " + emailBody

            try:
                sender = Emailer()
                sender.sendmail(sendTo, emailSubject, emailBody)
            except Exception as error:
                printWithTime("Tesla-Timer: Unable to send email because of exception: " + type(error).__name__ + ")")

            if (g_debug & 3) > 0:
                printWithTime(emailSubject)
                printWithTime("Tesla-Timer: " + emailBody)

        g_in_timer = 0
        return;
    
    printWithTime("Tesla-Timer: Timer Hang Debug: Tessie queried, getting vehicle parameters")

    vehicle_state = response.json().get("vehicle_state")
    climate_state = response.json().get("climate_state")
    charge_state  = response.json().get("charge_state")
    drive_state   = response.json().get("drive_state")
    
    if vehicle_state == None or drive_state == None or climate_state == None or charge_state == None:
        if g_already_sent_email_after_error == False:
            g_already_sent_email_after_error = True

            emailBody = "Missing data reading vehicle state for VIN " + vin
            emailSubject = "Tesla-Timer: " + emailBody

            try:
                sender = Emailer()
                sender.sendmail(sendTo, emailSubject, emailBody)
            except Exception as error:
                printWithTime("Tesla-Timer: Unable to send email because of exception: " + type(error).__name__ + ")")

            printWithTime("Tesla-Timer: " + emailBody)

        g_in_timer = 0
        return;

    g_already_sent_email_after_error = False; 

    g_moving = drive_state['shift_state']
    if g_moving is not None and g_moving != "P":
        if (g_debug & 3) > 0:
            printWithTime("Tesla-Timer: Vehicle in motion, skipping checking inside temperature and windows")
            g_in_timer = 0
            return

    fd_window = int(vehicle_state['fd_window'])
    fp_window = int(vehicle_state['fp_window'])
    rd_window = int(vehicle_state['rd_window'])
    rp_window = int(vehicle_state['rp_window'])
    g_windows = fd_window + fp_window + rd_window + rp_window # 0 means close so when we add them up, anything but 0 means at least a window is opened

    g_latitude  = drive_state['latitude']
    g_longitude = drive_state['longitude']
    
    if g_latitude == None or g_longitude == None:
        if (g_debug & 3) > 1:
            printWithTime("Tesla-Timer: Debug: Missing Latitude or Longitude, assuming we're at our station")
        latitude = station_latitude
        longitude = station_longitude
    else:
        latitude = g_latitude
        longitude = g_longitude
    
    printWithTime("Tesla-Timer: Timer Hang Debug: Finish building vehicle parameters, querying sun")

    # Check if the sun has set and if our windows are still opened
    sun = Sun(latitude, longitude)
    today_sr = sun.get_sunrise_time()
    today_ss = sun.get_sunset_time()
    if now.day == today_sr.day:
        tomorrow_sr = sun.get_sunrise_time() + timedelta(days=1)
    else:
        tomorrow_sr = sun.get_sunrise_time()
    
    tz_toronto = pytz.timezone('America/Toronto')
    now_tz = tz_toronto.localize(now)
    if today_sr > today_ss:
        today_ss = today_ss + timedelta(days=1) # Bug in the routine, day isn't update when crossing over midnight in UTC timezone

    if g_debug & 0x4000:
        printWithTime("Tesla-Timer: Debug: today_sr: " + str(today_sr))
        printWithTime("Tesla-Timer: Debug: today_ss: " + str(today_ss))
        printWithTime("Tesla-Timer: Debug: now_tz: " + str(now_tz))

    g_owm_raining = False # We assume it's not raining

    printWithTime("Tesla-Timer: Timer Hang Debug: Sun queried, analysing results")

    if today_sr > now_tz or now_tz > today_ss:
        printWithTime("Tesla-Timer: Timer Hang Debug: Doing night stuff")

        if (g_debug & 3) > 1:
            printWithTime("Tesla-Timer: Debug: It's night with " + str(tomorrow_sr - now_tz) + " until sunrize")
        if g_night == False or g_retry == 10:  # First time going in since the sun has set, check if we're parked with the windows down and if so, close them
            if g_retry == 10: # If we got here because we timed out, reset it back to 0
                g_retry = 0
            g_night = True
            if (g_debug & 3) > 0:
                printWithTime("Tesla-Timer: It's night, check if our windows are closed")
            if g_windows is not None and g_windows > 0:
                waitTime = g_wd_timer - 5
                if waitTime > 90:
                    waitTime = 90
                response = tessie("command/close_windows", "?retry_duration=" + str(waitTime), waitTime)
                status_code = response.status_code
                if status_code == 200:
                    
                    result = response.json().get("result")
                    woke = response.json().get("woke")
                    if result == True:
                        g_retry = 0
                        emailBody = "Closing windows because it's night time."

                        now = datetime.now()
                        current_time = now.strftime("%H:%M:%S")
                        emailSubject = "Tesla-Timer: Windows were opened at sunset (" + current_time + ")"
                    else:
                        g_retry = g_retry + 1
                        emailBody = "Unable to close windows at sunset. Check vehicle!"

                        emailSubject = "Tesla-Timer: " + emailBody
                else:
                    g_retry = g_retry + 1
                    emailBody = "Unable to close windows at sunset. Status code was " + str(status_code) + " Check vehicle!"

                    emailSubject = "Tesla-Timer: " + emailBody
                
                try:
                    sender = Emailer()
                    sender.sendmail(sendTo, emailSubject, emailBody)
                except Exception as error:
                    printWithTime("Tesla-Timer: Unable to send email because of exception: " + type(error).__name__ + ")")

                printWithTime(emailSubject)
                
            else:
                g_retry = 0
                if (g_debug & 3) > 0:
                    printWithTime("Tesla-Timer: Our windows are closed")
        elif g_retry != 0: # If we got an error when trying to close the windows, wait 10 iteration cycles and try again
            g_retry = g_retry + 1
    else:
        printWithTime("Tesla-Timer: Timer Hang Debug: Doing day stuff")
        g_night = False
        if (g_debug & 3) > 1:
            printWithTime("Tesla-Timer: Debug: It's daytime with " + str(today_ss - now_tz) + " until sunset")

    # Check if it's during the hottest part of the day, it's warm outside and the sun might be out - All conditions to have a warm inside. If set, keep the vehicle awake so cabin protection can do its stuff
    # Also check if raining according to OWM and close the windows if they are opened (location based on car's position), no matter the time of day
    printWithTime("Tesla-Timer: Timer Hang Debug: Doing OWM stuff")

    if owm_key is not None:
        URL = "https://api.openweathermap.org/data/2.5/weather?lat=" + str(latitude) + "&lon=" + str(longitude) + "&appid=" + str(owm_key)
        if g_debug & 0x100:
            printWithTime("OWM URL = " + URL)

        response = requests.get(URL)
        printWithTime("Tesla-Timer: Timer Hang Debug: OWM queried, analysing results")
        if response.status_code == 200:
            if g_debug & 0x200:
                printWithTime(json.dumps(response.json(), indent = 4))
            data = response.json()

            # Favor the car temperature
            g_out_temp = None
            if vehicle_status == "awake":
                g_out_temp = climate_state['outside_temp']
                if (g_debug & 3) > 1:
                    if g_out_temp is None:
                        printWithTime("Tesla-Timer: Debug: Can't read the car's outside temperature")
                    else:
                        printWithTime("Tesla-Timer: Debug: Outside temperature according to the car is " + "{:.1f}".format(g_out_temp) + "C")
            if g_out_temp is None and "temp" in data['main']:
                g_out_temp = float(data['main']['temp']) - 273.15
                if (g_debug & 3) > 1:
                    printWithTime("Tesla-Timer: Debug: Outside temperature according to OWM station '" + data['name'] + "' is " + "{:.1f}".format(g_out_temp) + "C")

            if g_windows is not None and g_windows > 0:
                if (g_debug & 3) > 0:
                    printWithTime("Tesla-Timer: Windows are opened")
            
            if vehicle_status == "awake":
                if g_debug & 0x800:
                    printWithTime("Tesla-Timer: Debug: Climate is " + json.dumps(climate_data, indent = 4))
                inside_temp = climate_state['inside_temp']
                active_cooling =  climate_state['cabin_overheat_protection_actively_cooling']

            icon = data['weather'][0]['icon']
            if int(icon[0:2]) < 4 and str(icon[2:3]) == "d": # Icon with a number lower than 4 means there is some sun showing and 'd' means it's daytime
                if today_sr + timedelta(hours=3) < now_tz < today_ss - timedelta(hours=3): # Sun is up high enough in the sky
                    if g_out_temp is not None and g_out_temp > 10.0: # Below 10C means it's not hot enough to overheat the cabin
                        # Before we go any further, we must make sure the battery level is at least 20% to prevent running down the battery too much
                        soc = charge_state['battery_level']
                        if soc is not None and soc >= 20:
                            if vehicle_status == "awake":
                                #vehicles[vehicle].sync_wake_up()  # Keep the vehicle awake so cabin overheat protection can do its stuff if needed <- Only works for 12 hours after a drive, not when awaken :-(
                                if (g_debug & 3) > 1:
                                    printWithTime("Tesla-Timer: Debug: Fan is running: " + str(active_cooling))
                                    printWithTime("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") according to OWM station '" + data['name'] + "' during mid-day and outside is warm at " + "{:.1f}".format(g_out_temp) + "C with inside at " + "{:.1f}".format(inside_temp) + "C - The vehicle is awake!")
                            else:
                                if (g_debug & 3) > 1:
                                    printWithTime("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") according to OWM station '" + data['name'] + "' during mid-day and outside is warm at " + "{:.1f}".format(g_out_temp) + "C - Vehicle is asleep so can't get its inside temperature")
                        else:
                            if (g_debug & 3) > 1:
                                printWithTime("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") according to OWM station '" + data['name'] + "' during mid-day and outside is warm at " + "{:.1f}".format(g_out_temp) + "C but not waking the vehicle because SoC at " + str(soc) + "%")
                    else:
                        if (g_debug & 3) > 1:
                            if g_out_temp is not None:
                                printWithTime("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") according to OWM station '" + data['name'] + "' during mid-day and outside is cold at " + "{:.1f}".format(g_out_temp) + "C")
                            else:
                                printWithTime("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") according to OWM station '" + data['name'] + "' during mid-day and can't read the outside temperature")
                else:
                    if (g_debug & 3) > 1:
                        if vehicle_status == "awake":
                            if inside_temp is not None:
                                printWithTime("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") according to OWM station '" + data['name'] + "' but too early or late to be warm enough in the car. Inside temperature is " + "{:.1f}".format(inside_temp) + "C - The vehicle is awake!")
                            else:
                                printWithTime("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") according to OWM station '" + data['name'] + "' but too early or late to be warm enough in the car. Unable to read the inside temperature - The vehicle is awake!")
                        else:
                            printWithTime("Tesla-Timer: Debug: Some sun at least with " + data['weather'][0]['description'] + " (" + str(icon) + ") according to OWM station '" + data['name'] + "' but too early or late to be warm enough in the car - The vehicle is sleeping")
            else: # It's not a clear sky or it's night
                if int(icon[0:2]) >= 9 and int(icon[0:2]) <= 11: # But is it raining? 9: Shower rain, 10: Rain, 11: Thunderstorm
                    if g_mqtt_raining == False and g_owm_raining == False: # We'll reset to False once the rain has stopped, so we don't keep pounding the vehicle for the same rain shower
                        g_owm_raining = True # It's raining according to OWM, let's check our windows (and MQTT hasn't seen rain yet)
                        if (g_debug & 3) > 1:
                            printWithTime("Tesla-Timer: Debug: Calling raining_check_windows")
                        raining_check_windows(-1.0, data['name'])
                        if (g_debug & 3) > 1:
                            printWithTime("Tesla-Timer: Debug: Returning from raining_check_windows")
                    else:
                        if (g_debug & 3) > 0:
                            printWithTime("Tesla-Timer: Skipping, waiting for the rain to stop")

                if (g_debug & 3) > 1:
                    if vehicle_status == "awake":
                        if (g_debug & 3) > 2:
                            if inside_temp is not None:
                                printWithTime("Tesla-Timer: Debug: Sun shouldn't be visible with " + data['weather'][0]['description'] + " (" + str(icon) + ") according to OWM station '" + data['name'] + "' during mid-day and outside is warm at " + "{:.1f}".format(g_out_temp) + "C with inside at " + "{:.1f}".format(inside_temp) + "C - The vehicle is awake!")
                            else:
                                printWithTime("Tesla-Timer: Debug: Sun shouldn't be visible with " + data['weather'][0]['description'] + " (" + str(icon) + ") according to OWM station '" + data['name'] + "' during mid-day and outside is warm at " + "{:.1f}".format(g_out_temp) + "C and unable to read the inside temperature - The vehicle is awake!")
                    else:
                        printWithTime("Tesla-Timer: Debug: Sun shouldn't be visible with " + data['weather'][0]['description'] + " (" + str(icon) + ") according to OWM station '" + data['name'] + "' - The vehicle is sleeping")
        else:
            if (g_debug & 3) > 1:
                printWithTime("Tesla-Timer: Debug: OWN returned " + str(response.status_code))
    elif (g_debug & 3) > 1:
        printWithTime("Tesla-Timer: Debug: No OWM token")
    printWithTime("Tesla-Timer: Timer Hang Debug: Finished OWM stuff")

    g_in_timer = 0

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

g_t_sec = int(Config.get('Timers', 'Timer'))
g_wd_timer = int(Config.get('Timers', 'WatchDog'))
g_wd_mqtt_max = int(Config.get('Timers', 'MQTT_Max'))
g_debug = int(Config.get('Debug', 'Debug_level'))
print("Tesla: Debug level is " + str(g_debug))

max_distance = float(Config.get('MQTT', 'max_distance'))
if Config.has_option('OWM', 'api_key'):
    print("Tesla: Will use OWM")
    owm_key = Config.get('OWM', 'api_key')
else:
    print("Tesla: Will NOT use OWM")
    owm_key = None
    
g_mqtt_lastRun = datetime.now()
g_mqtt_ran = True
g_timer_lastRun = datetime.now()
g_timer_ran = True
g_night = False
g_out_temp = None
g_already_sent_email_after_error = False
g_retry = 0
g_in_timer = 0
g_timeout_count = 0

# These are our Tesla data we need to keep while we're running
g_windows = None
g_moving = None
g_latitude = None
g_longitude = None
g_mqtt_raining = False
g_owm_raining = False
g_kill_prog = False

# Set up our MQTT connection if we have something
#breakpoint() ############################################## BREAKPOINT ##############################################
if Config.has_option('MQTT', 'hostname'):
    g_skip_mqtt = False
    
    mqtt_client = mqtt.Client()
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message

    if Config.getboolean('MQTT', 'use_tls') == True:
        mqtt_client.tls_set()
    mqtt_client.username_pw_set(username = Config.get('MQTT', 'username'), password = Config.get('MQTT', 'password'))

    print("Tesla: Connecting to MQTT...")
    mqtt_client.connect(Config.get('MQTT', 'hostname'), int(Config.get('MQTT', 'port')), 60)
elif owm_key is not None:
    g_skip_mqtt = True
    print("Tesla: Skipping MQTT, will only use OWM")
else:
    print("Tesla: No MQTT and no OWM, what are we supposed to do here? Quitting")
    quit(1)

print("Tesla: Checking vehicle's status")
vehicle_status = get_vehicle_status()
if (vehicle_status == "asleep" or vehicle_status == "waiting_for_sleep"):
    if wake_at_start == 1:
        print("Waking up vehicle " + vin)
        response = tessie("wake", "", g_wd_timer)
    else:
        print("Vehicle " + vin + " is asleep and we're not requesting it to be waken up")
elif vehicle_status == "awake":
    print("Vehicle " + vin + " is already awake")
else:
    print("Vehicle " + vin + " returned a status of " + vehicle_status)

print("Running first instance of the timer thread")
on_timer()

print("Tesla: Starting timer thread with an interval of " + str(g_t_sec) + " seconds")
T = RepeatTimer(g_t_sec, on_timer)
T.start()

print("Tesla: Waiting 30 seconds before starting watchdog thread")
time.sleep(30)

print("Tesla: Starting watchdog thread with an interval of " + str(g_wd_timer) + " seconds and max time between MQTT event is " + str(g_wd_mqtt_max))
W = RepeatTimer(g_wd_timer, on_watchdog)
W.start()

if g_skip_mqtt:
    loop = asyncio.get_event_loop()
    try:
        loop.run_forever()
    finally:
        loop.close()
else:
    mqtt_client.loop_forever()
