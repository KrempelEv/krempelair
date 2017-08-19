#!/usr/bin/env python3
# -*- coding: utf-8 -*-



import sys
sys.path.append('/opt/krempel/krempelair/krempelair/lib/bus/')

import time
import json
import requests
from digitalInOut import digiInOut
from analogInOut import analogInOut

digitalPins = digiInOut()
analogPins = analogInOut()

betrieb = False
frost = False

stellsignal = 0
stellsignalWRG = 0
stellsignalLE = 0

error = 0
errorSum = 0

while True:
    # Abfragen ob die Lüftung in Betrieb ist:
    betriebsByte = digitalPins.getValue(0x21)
    if betriebsByte&1 != 0:
        print ("Läuft")
        betrieb = True
    else:
        betrieb = False
        print ("Läuft nicht")
    # Abfragen ob die Lüftung Frostalarm hat:
    stoerungsByte = digitalPins.getValue(0x22)
    if stoerungsByte&4 != 0:
        print ("Frost")
        frost = True
    else:
        frost = False


    myResponse = requests.get("http://192.168.1.100/api/lueftung/temperatur")
    if(myResponse.ok):
        # Messwere
        tempZul = myResponse.json()["ZUL"]
        tempAbl = myResponse.json()["ABL"]
        tempAul = myResponse.json()["AUL"]
        tempFol = myResponse.json()["FOL"]
        tempWitt = myResponse.json()["TempWitt"]
        tempRaum = myResponse.json()["RaumVorne"]
        # Sollwerte
        tempSoll = myResponse.json()["TempSoll"]
        tempZulMin = myResponse.json()["tempZulMin"]
        tempZulMax = myResponse.json()["tempZulMax"]
        tempSollNAK = myResponse.json()["TempSollNAK"]

    tempIst = (tempRaum + tempAbl)/2
    stellsignal
    # Case Selector
    # 0  Aus
    # 1  Heizen
    # 11
    #
    #
    #
    #

    if(betrieb):
        print("Betrieb")
        print("tempSoll =",tempSoll)
        print("tempIst =",tempIst)
        error = tempSoll - tempIst

        if(error > 0):
            print("Heizen um ",error, "Grad")
        else:
            print("Kühlen um ",error,"Grad")


        errorSum = errorSum + error
        y = 10*error+5*0.1*errorSum
        stellsignal = stellsignal + y

        if(stellsignal >  2046):
            stellsignal = 2046
            errorSum = 0
        if(stellsignal < 0):
            stellsignal = 0

        stellsignalWRG = stellsignal
        if(stellsignalWRG > 1023):
            stellsignalWRG = 1023
        stellsignalLE = stellsignal - 1023
        if(stellsignalLE < 0):
            stellsignalLE = 0

        print("StellsignalWRG = ",stellsignalWRG)
        print("StellsignalLE = ",stellsignalLE)

        analogPins.setValue(0x58,0x00,stellsignalWRG)
        analogPins.setValue(0x58,0x01,stellsignalLE)


    time.sleep(2)

