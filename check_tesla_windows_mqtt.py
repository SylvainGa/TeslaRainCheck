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

# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    print("Tesla: Connected to MQTT with result code "+str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("acurite/loop")

# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    #print(msg.topic+" "+str(msg.payload.decode('utf-8')))
    jsondata = json.loads(str(msg.payload.decode('utf-8')))
    rain_cm = jsondata.get('rain_cm')

    #rain_cm = "0.01"	# To test the code when it rains

    global Config
    global next_run
    global tesla
    global vehicles
    global vehicle
    global moving
    global windows
    global raining
    global lastRun
    global ran
    global night
    global out_temp
 
    if jsondata.get('outTemp_C') is not None:
        out_temp = float(jsondata.get('outTemp_C'))

    now = datetime.now()
    lastRun = now
    ran = True
    current_time = now.strftime("%H:%M:%S")
    
    if now >= next_run:  # We do a run after 5 minutes
        #print("Debug: Times up, querying the car")

        # Connect to our car (at least once) to get the status of the windows
        if tesla is None:
            print("Tesla: Connecting to car")
            tesla = teslapy.Tesla(Config.get('Tesla', 'username'), retry=3, timeout=20)
            print("Tesla: Connected")

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

        available = False
        vehicles = tesla.vehicle_list()
        #print("Debug: Looking for vehicle " + str(vehicle))
        if vehicles[vehicle]['state'] != 'asleep':
            available = True
        if windows < 0: # -1 means we never ran, so fetch real data, wake up the car if it's asleep
            if available == False: # Wake the car if asleep
                vehicles[vehicle].sync_wake_up()  # We need to get up to date data so no choice but to wake it
        #print("Debug: available is " + str(available))
        if available == True: # Only read if the car isn't asleep
            vehicleData = vehicles[vehicle].get_vehicle_data()
            fd_window = int(vehicleData['vehicle_state']['fd_window'])
            fp_window = int(vehicleData['vehicle_state']['fp_window'])
            rd_window = int(vehicleData['vehicle_state']['rd_window'])
            rp_window = int(vehicleData['vehicle_state']['rp_window'])
            windows = fd_window + fp_window + rd_window + rp_window # 0 means close so when we add them up, anything but 0 means at least a window is opened

            moving = vehicleData['drive_state']['shift_state']
            if moving is None:
                # Check how far from our station are we
                latitude=vehicleData['drive_state']['latitude']
                longitude=vehicleData['drive_state']['longitude']

                # Now check if we're close to our station. If not, poll at a lower frequency
                station = (station_latitude, station_longitude)
                car = (float(latitude), float(longitude))
                distance = float(geopy.distance.geodesic(station, car).km)
                #print("Debug: We're " + str(distance) + " km away")
                if distance < max_distance:
                    next_run = now + timedelta(minutes = 1)	# Check every minute when the car is close to our station to reduce the chance of missing a window opening before going to sleep
                else:
                    next_run = now + timedelta(minutes = 5)	# Check every five minutes when the car is far from our station

                inside_temp = vehicleData['climate_state']['inside_temp']
                #print("Debug: Inside temperature is " + str(inside_temp) + "C")
                if float(inside_temp) > 40.0:
                    sender = Emailer()
                    emailBody = "Inside temperature is " + str(inside_temp) + "C"
                    emailSubject = "Tesla: WeeWX - Inside temperature too hot at " + str(inside_temp) + "C"
                    sender.sendmail(sendTo, emailSubject, emailBody)
                    print(emailSubject)
            else:
                next_run = now + timedelta(minutes = 5)	# Check every five minutes when the car is not parked. With 5 minutes interval, we're sure to hit the 'Park' spot before it goes to sleep (roughly 10 minutes)

            #print("Tesla: Number of windows open: " + str(windows) + " - Moving is: " + str(moving))
        else:
            next_run = now + timedelta(minutes = 5)	# We're asleep so check back in 5 minutes
            #print("Tesla: Vehicle is sleeping, shhh")

        # Check if the sun has set and our windows are still opened
        sun = Sun(station_latitude, station_longitude)
        today_ss = sun.get_sunset_time()
        today_sr = sun.get_sunrise_time()
        tz_toronto = pytz.timezone('America/Toronto')
        now_tz = tz_toronto.localize(now)
        #print("Debug: today_ss: " + str(today_ss))
        #print("Debug: now_tz: " + str(now_tz))
        if now_tz > today_ss:
            if night == False:  # First time going in since the sun has set, check if we're parked with the windows down and if so, close them
                if moving is None:
                    night = True
                    if  windows > 0:
                        if available == False:    # Wake the car if asleep
                            vehicles[vehicle].sync_wake_up()  # We need to get up to date data so no choice but to wake it

                        vehicleData = vehicles[vehicle].get_vehicle_data()
                        latitude=vehicleData['drive_state']['latitude']
                        longitude=vehicleData['drive_state']['longitude']

                        vehicles[vehicle].command('WINDOW_CONTROL', command='close', lat=latitude, lon=longitude)
                        emailBody = "Closing windows because it's night time."

                        sender = Emailer()
                        emailSubject = "Tesla: WeeWX - Windows were opened at sunset (" + current_time + ")"
                        sender.sendmail(sendTo, emailSubject, emailBody)
                        
                        print(emailSubject)
                        print("Tesla: " + emailBody)
        else:
            night = False
            #print("Debug: Still daytime with diff of " + str(today_ss - now_tz))

        # Check if it's during the hottest part of the day, it's warm outside and the sun might be out - All conditions to have a warm inside
        if owm_key is not None:
            URL = "https://api.openweathermap.org/data/2.5/weather?lat=" + str(station_latitude) + "&lon=" + str(station_longitude) + "&appid=" + str(owm_key)
            response = requests.get(URL)
            if response.status_code == 200:
                data = response.json()
                icon = data['weather'][0]['icon']
                if int(icon[0:2]) < 5: # Icon with a number lower than 5 means there is some sun showing
                    if today_sr + timedelta(hours=3) < now_tz < today_ss - timedelta(hours=3): # Sun is up high enough in the sky
                        if out_temp > 10.0: # Below 10C means it's not hot enough to overheat the cabin
                            vehicles[vehicle].sync_wake_up()  # Keep the car awake so cabin overheat protection can do its stuff if needed
                            print("Debug: Some sun at least with " + data['weather'][0]['description'] + " during mid-day and outside is warm at " + str(out_temp) + "C, keeping the car awake so cabin overheat can do its job")
                        else:
                            print("Debug: Some sun at least with " + data['weather'][0]['description'] + " during mid-day and outside is cold at " + str(out_temp) + "C")
                    else:
                        print("Debug: Some sun at least with " + data['weather'][0]['description'] + " but too early or late to be warm enough")
                else:
                    print("Debug: Sun shouldn't be visible with " + data['weather'][0]['description'])
            else:
                print("Debug: OWN returned " + str(response.status_code))

    # Read how much rain as fallen
    if rain_cm is not None:
        #print("Tesla: " + current_time + ": " + rain_cm + " cm")

        rain = float(rain_cm)
        if rain > 0.0:
            if raining == False:    # We'll reset to False once the rain has stopped, so we don't keep pounding the car for the same rain shower
                raining = True

                available = False
                vehicles = tesla.vehicle_list()
                if vehicles[vehicle]['state'] != 'asleep':
                    available = True
                if available == True: # If we're online, get fresh data
                    vehicleData = vehicles[vehicle].get_vehicle_data()
                    moving = vehicleData['drive_state']['shift_state']
                    fd_window = int(vehicleData['vehicle_state']['fd_window'])
                    fp_window = int(vehicleData['vehicle_state']['fp_window'])
                    rd_window = int(vehicleData['vehicle_state']['rd_window'])
                    rp_window = int(vehicleData['vehicle_state']['rp_window'])
                    windows = fd_window + fp_window + rd_window + rp_window # 0 means close so when we add them up, anything but 0 means at least a window is opened
                if moving is None and windows > 0:
                    if available == False:    # Wake the car if asleep
                        vehicles[vehicle].sync_wake_up()  # We need to get up to date data so no choice but to wake it

                    vehicleData = vehicles[vehicle].get_vehicle_data()
                    latitude=vehicleData['drive_state']['latitude']
                    longitude=vehicleData['drive_state']['longitude']

                    # Now check if we're close to our station. If not, ignore the rain
                    station = (station_latitude, station_longitude)
                    car = (float(latitude), float(longitude))
                    distance = float(geopy.distance.geodesic(station, car).km)
                    if distance < max_distance:
                        vehicles[vehicle].command('WINDOW_CONTROL', command='close', lat=latitude, lon=longitude)
                        emailBody = "We're parked close enough to our station with our windows opened in the rain! Closing them"
                    else:
                        emailBody = "We're parked with our windows opened in the rain but too far (" + "%.1f" % distance + " km) to be sure it's raining on us, so leaving as is"

                    emailSubject = "Tesla: WeeWX - It has rained " + rain_cm + " cm at " + current_time
                    sender = Emailer()
                    sender.sendmail(sendTo, emailSubject, emailBody)
                
                    print(emailSubject)
                    print("Tesla: " + emailBody)
                else:
                    print("Tesla: WeeWX - It has rained " + rain_cm + " cm at " + current_time + " and our windows are closed")
            #else:
                #print("Tesla: Already checked for this rain period, skipping")
        else:
            raining = False
            #print("Tesla: All is fine")
    else:
        now = datetime.now()
        current_time = now.strftime("%H:%M:%S")
        #print("Tesla: " + current_time + " No rain data, skipping")

class RepeatTimer(Timer):
    def run(self):
        while not self.finished.wait(self.interval):
            self.function(*self.args, **self.kwargs)

def timer():
    global lastRun
    global ran

    now = datetime.now()
    current_time = now.strftime("%H:%M:%S")
    #print("Tesla: " + current_time + ": Last ran at " + lastRun.strftime("%H:%M:%S"))

    if lastRun + timedelta(seconds = 60) < now:
        if ran == True:
            ran = False

            emailBody = "Last ran at " + lastRun.strftime("%H:%M:%S")

            sender = Emailer()
            emailSubject = "Tesla: WeeWX - MQTT thread hasn't ran in over a minute"
            sender.sendmail(sendTo, emailSubject, emailBody)
                
            print(emailSubject)

####### Start here

# Read our config
Config = configparser.ConfigParser()
Config.read("check_tesla_windows_mqtt.ini")

# Initialise our global variables
raining = False
tesla = None
vehicles = None
vehicle = 0
moving = None	# Assuming not moving when we start the code
next_run = datetime.now()
vehicle = int(Config.get('Tesla', 'vehicle'))
windows = int(Config.get('Tesla', 'windows'))	# Reading initial states of windows. -1 means wake the car to read, 0 means assume they are closed, anything else assume they are open
sendTo = Config.get('Email', 'to')
station_latitude = float(Config.get('MQTT', 'latitude'))
station_longitude = float(Config.get('MQTT', 'longitude'))
max_distance = float(Config.get('MQTT', 'max_distance'))
owm_key = Config.get('OWM', 'api_key')

lastRun = datetime.now()
ran = True
night = False
out_temp = 0

# Set up our MQTT connection
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

if Config.getboolean('MQTT', 'use_tls') == True:
    client.tls_set()
client.username_pw_set(username = Config.get('MQTT', 'username'), password = Config.get('MQTT', 'password'))

print("Tesla: Connecting to MQTT...")
client.connect(Config.get('MQTT', 'hostname'), int(Config.get('MQTT', 'port')), 60)

print("Tesla: Starting watchdog thread with interval of 60 seconds")
T = RepeatTimer(60, timer)
T.start()

# Blocking call that processes network traffic, dispatches callbacks and
# handles reconnecting.
# Other loop*() functions are available that give a threaded interface and a
# manual interface.
client.loop_forever()
