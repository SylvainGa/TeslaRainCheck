#!/usr/bin/env python3

import sys
sys.path.insert(0, '/home/pi/tesla/TeslaPy')

import teslapy
import paho.mqtt.client as mqtt
import json
import time
from datetime import datetime
import smtplib

class Emailer:
    def sendmail(self, recipient, subject, content):
        SMTP_SERVER = 'smtp.gmail.com' #Email Server (don't change!)
        SMTP_PORT = 587 #Server Port (don't change!)
        GMAIL_USERNAME = 'username@gmail.com' #change this to match your gmail account used to send the email from
        GMAIL_PASSWORD = 'password' #change this to match your gmail password. Support Google App passord

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

sendTo = 'username@gmail.com' # Email account receiving the emails

raining = False
first_run = True

# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    print("Connected with result code "+str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("acurite/loop")

# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
#    print(msg.topic+" "+str(msg.payload.decode('utf-8')))
    jsondata = json.loads(str(msg.payload.decode('utf-8')))
    rain_cm = jsondata.get('rain_cm')

    if rain_cm is not None:
        now = datetime.now()
        current_time = now.strftime("%H:%M:%S")
        print(current_time + ": " + rain_cm)

        if first_run == True:
#            first_run = false
            print("Sending first run email")

            sender = Emailer()
            emailSubject = "Tesla WeeWX - Got our first set of data of " + rain_cm + "cm at " + current_time
            emailContent = "Tesla WeeWX - Got our first set of data of " + rain_cm + "cm at " + current_time
            sender.sendmail(sendTo, emailSubject, emailContent)
        
        rain = float(rain_cm)
        if rain > 0.0:
            if raining == False:
                raining = True
                print("Reading car")
                tesla = teslapy.Tesla('username@example.com')
                if not tesla.authorized:
                    sender = Emailer()
                    emailSubject = "Tesla WeeWX -  Require a new token at " + current_time
                    emailContent = "Tesla WeeWX -  My token is currently '" + tesla.refresh_token + "'"
                    sender.sendmail(sendTo, emailSubject, emailContent)

                    tesla.refresh_token(refresh_token=input('Enter SSO refresh token: '))
                vehicles = tesla.vehicle_list()
                vehicles[0].sync_wake_up()  # We need to get up to date data so no choice but to wake it
                vehicleData = vehicles[0].get_vehicle_data()

                fd_window = int(vehicleData['vehicle_state']['fd_window'])
                fp_window = int(vehicleData['vehicle_state']['fp_window'])
                rd_window = int(vehicleData['vehicle_state']['rd_window'])
                rp_window = int(vehicleData['vehicle_state']['rp_window'])
                windows = fd_window + fp_window + rd_window + rp_window
                print('Number of windows open: ' + str(windows))

                moving = vehicleData['drive_state']['shift_state']
                print('Driving: ' + str(moving))

                if moving is None and windows > 0:
                    print("Closing windows!")

                    latitude=vehicleData['drive_state']['latitude']
                    longitude=vehicleData['drive_state']['longitude']

                    print(latitude)
                    print(longitude)
                    vehicles[0].command('WINDOW_CONTROL', command='close', lat=latitude, lon=longitude)
                else:
                    print("It's raining but our windows are closed")

                tesla.close()
            else:
                print("Already checked for this rain period, skipping")
        else:
            raining = False
            print("All is fine")

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

#client.tls_set()   # Uncomment if your mqtt installation uses TLS (without TLS, username and password are sent in clear text over your network
client.username_pw_set(username="username", password="password")

print("Connecting...")
client.connect("hostname", 1883, 60)

# Blocking call that processes network traffic, dispatches callbacks and
# handles reconnecting.
# Other loop*() functions are available that give a threaded interface and a
# manual interface.
client.loop_forever()
