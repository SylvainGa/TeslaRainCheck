#!/usr/bin/env python3

import sys
sys.path.insert(0, './TeslaPy')

import teslapy
import json
import time
import smtplib
import configparser
import paho.mqtt.client as mqtt
from datetime import datetime, timedelta

class Emailer:
    def sendmail(self, recipient, subject, content):
        SMTP_SERVER = 'smtp.gmail.com' #Email Server (don't change!)
        SMTP_PORT = 587 #Server Port (don't change!)
        GMAIL_USERNAME = Config.get('Email', 'username') #change this to match your gmail account
        GMAIL_PASSWORD = Config.get('Email', 'password') #change this to match your gmail password
        #GMAIL_PASSWORD = 'Hi9zeepeet9n' #change this to match your gmail password

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

    global next_run
    global tesla
    global vehicles
    global vehicle
    global moving
    global windows
    global raining

    now = datetime.now()
    if now >= next_run:  # We do a run after 5 minutes
        #print("Debug: Times up, querying the car")

        # Connect to our car (at least once) to get the status of the windows
        if tesla is None:
            print("Tesla: Connecting to car")
            tesla = teslapy.Tesla(Config.get('Tesla', 'username'))

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

        vehicles = tesla.vehicle_list()
        #print("Debug: Looking for vehicle " + str(vehicle))
        if windows < 0: # -1 means we never ran, so fetch real data, wake up the car if it's asleep
            if vehicles[vehicle].available() == False: # Wake the car if asleep
                vehicles[vehicle].sync_wake_up()  # We need to get up to date data so no choice but to wake it
        #print("Debug: available is " + str(vehicles[vehicle].available()))
        if vehicles[vehicle].available() == True: # Only read if the car isn't asleep
            next_run = now + timedelta(minutes = 1)	# Check every minute when the car is awake to reduce the chance of missing a window opening

            vehicleData = vehicles[vehicle].get_vehicle_data()
            moving = vehicleData['drive_state']['shift_state']
            fd_window = int(vehicleData['vehicle_state']['fd_window'])
            fp_window = int(vehicleData['vehicle_state']['fp_window'])
            rd_window = int(vehicleData['vehicle_state']['rd_window'])
            rp_window = int(vehicleData['vehicle_state']['rp_window'])
            windows = fd_window + fp_window + rd_window + rp_window # 0 means close so when we add them up, anything but 0 means at least a window is opened
            
            print("Tesla: Number of windows open: " + str(windows) + " Moving is " + str(moving))
        else:
            next_run = now + timedelta(minutes = 5)	# We're asleep so check back in 5 minutes

            print("Tesla: Vehicle is sleeping, shhh")
    # Read how much rain as fallen
    if rain_cm is not None:
        now = datetime.now()
        current_time = now.strftime("%H:%M:%S")
        print("Tesla: " + current_time + ": " + rain_cm + " cm")

        rain = float(rain_cm)
        if rain > 0.0:
            if raining == False:    # We'll reset to False once the rain has stopped, so we don't keep pounding the car for the same rain shower
                raining = True

                if vehicles[vehicle].available() == True: # If we're online, get fresh data
                    vehicleData = vehicles[vehicle].get_vehicle_data()
                    moving = vehicleData['drive_state']['shift_state']
                    fd_window = int(vehicleData['vehicle_state']['fd_window'])
                    fp_window = int(vehicleData['vehicle_state']['fp_window'])
                    rd_window = int(vehicleData['vehicle_state']['rd_window'])
                    rp_window = int(vehicleData['vehicle_state']['rp_window'])
                    windows = fd_window + fp_window + rd_window + rp_window # 0 means close so when we add them up, anything but 0 means at least a window is opened

                if moving is None and windows > 0:
                    emailBody = "We're parked with our windows opened in the rain! Closing them"

                    if vehicles[vehicle].available() == False:    # Wake the car if asleep
                        vehicles[vehicle].sync_wake_up()  # We need to get up to date data so no choice but to wake it

                    vehicleData = vehicles[vehicle].get_vehicle_data()
                    latitude=vehicleData['drive_state']['latitude']
                    longitude=vehicleData['drive_state']['longitude']
                    vehicles[vehicle].command('WINDOW_CONTROL', command='close', lat=latitude, lon=longitude)
                else:
                    emailBody = "It's raining but our windows are closed"

                sender = Emailer()
                emailSubject = "Tesla: WeeWX - It has rained " + rain_cm + " cm at " + current_time
                sender.sendmail(sendTo, emailSubject, emailBody)
                
                print(emailSubject)
                print("Tesla: " + emailBody)
            else:
                print("Tesla: Already checked for this rain period, skipping")
        else:
            raining = False
            print("Tesla: All is fine")
    else:
        now = datetime.now()
        current_time = now.strftime("%H:%M:%S")
        print("Tesla: " + current_time + " No rain data, skipping")

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

# Set up our MQTT connection
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

if Config.getboolean('MQTT', 'use_tls') == True:
    client.tls_set()
client.username_pw_set(username = Config.get('MQTT', 'username'), password = Config.get('MQTT', 'password'))

print("Tesla: Connecting to MQTT...")
client.connect(Config.get('MQTT', 'hostname'), int(Config.get('MQTT', 'port')), 60)

# Blocking call that processes network traffic, dispatches callbacks and
# handles reconnecting.
# Other loop*() functions are available that give a threaded interface and a
# manual interface.
client.loop_forever()
