# -*- coding: utf-8 -*-
# Python script (runs on 2 and 3) to monitor cpu load, temperature, frequency, free space etc.
# on a Raspberry Pi or Ubuntu computer and publish the data to a MQTT server.
# RUN sudo apt-get install python-pip
# RUN pip install paho-mqtt

from __future__ import division
import subprocess
import time
import socket
import paho.mqtt.client as paho
import json
import os
import sys
import argparse
import threading
import update
import config
import re
import html
import uuid
import glob
#import external sensor lib only if one uses external sensors
if config.ext_sensors:
    # append folder ext_sensor_lib
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ext_sensor_lib')))
    for item in config.ext_sensors:
        # only import the libraries which are required
        if item[1] == "ds18b20":
            import ds18b20
        if item[1] == "sht21":
            from sht21 import SHT21
        if item[1] == "x1202":
            from x1202 import X1202

def check_wifi_signal(format):
    try:
        full_cmd =  "ls /sys/class/ieee80211/*/device/net/"
        interface = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0].strip().decode("utf-8")
        full_cmd = "/sbin/iwconfig {} | grep -i quality".format(interface)
        wifi_signal = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0]

        if format == 'dbm':
            wifi_signal = wifi_signal.decode("utf-8").strip().split(' ')[4].split('=')[1]
        else:
            wifi_signal = wifi_signal.decode("utf-8").strip().split(' ')[1].split('=')[1].split('/')[0]
            wifi_signal = round((int(wifi_signal) / 70)* 100)

    except Exception:
        wifi_signal = 0

    return wifi_signal


def check_used_space(path):
    st = os.statvfs(path)
    free_space = st.f_bavail * st.f_frsize
    total_space = st.f_blocks * st.f_frsize
    used_space = int(100 - ((free_space / total_space) * 100))

    return used_space


def check_cpu_load():
    p = subprocess.Popen("uptime", shell=True, stdout=subprocess.PIPE).communicate()[0]
    cores = subprocess.Popen("nproc", shell=True, stdout=subprocess.PIPE).communicate()[0]
    try:
        cpu_load = str(p).split("average:")[1].split(", ")[0].replace(' ', '').replace(',', '.')
        cpu_load = float(cpu_load) / int(cores) * 100
        cpu_load = round(float(cpu_load), 1)
    except Exception:
        cpu_load = 0

    return cpu_load


def check_voltage():
    try:
        full_cmd = "vcgencmd measure_volts | cut -f2 -d= | sed 's/000//'"
        voltage = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()[0]
        voltage = voltage.strip()[:-1]
    except Exception:
        voltage = 0

    return voltage.decode('utf8')


def check_swap():
    full_cmd = "free | grep -i swap | awk 'NR == 1 {if($2 > 0) {print $3/$2*100} else {print 0}}'"
    swap = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0]
    swap = round(float(swap.decode("utf-8").replace(",", ".")), 1)

    return swap


def check_memory():
    full_cmd = 'free -b | awk \'NR==2 {printf "%.2f\\n", $3/$2 * 100}\''
    memory = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0]

    if memory:
        memory = round(float(memory.decode("utf-8").replace(",", ".")))
    else:
        memory = 0

    return memory


def check_rpi_power_status():
    try:
        full_cmd = "vcgencmd get_throttled | cut -d= -f2"
        throttled = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0]
        throttled = throttled.decode('utf-8').strip()
        
        if throttled == "0x0":
            return "OK"
        else:
            return "KO"
    except Exception as e:
        return "Error: " + str(e)

def read_ext_sensors():
    """
    here we read the external sensors
    we create a list with the external sensors where we append the values

    """
    # we copy the variable from the config file and replace the values by the real sensor values
    ext_sensors = config.ext_sensors
    # item[0] = name
    # item[1] = sensor_type
    # item[2] = ID
    # item[3] = value
    # now we iterate over the external sensors
    for item in config.ext_sensors:
        # if it is a DS18B20 sensor 
        if item[1] == "ds18b20":
            # if sensor ID in unknown, then we try to get it
            # this only works for a single DS18B20 sensor
            if item[2]==0:
                item[2] = ds18b20.get_available_sensors()[0]
            temp = ds18b20.sensor_DS18B20(sensor_id=item[2])
            item[3] = temp
            # in case that some error occurs during reading, we get -300
            if temp==-300:
                print ("Error while reading sensor %s, %s" % (item[1], item[2]))
        if item[1] == "sht21":
            try:
                with SHT21(1) as sht21:
                    temp = sht21.read_temperature()
                    temp = '%2.1f' % temp
                    hum = sht21.read_humidity()
                    hum = '%2.1f' % hum
                    item[3] = [temp, hum]
            # in case we have any problems to read the sensor, we continue and keep default values
            except Exception:
                print ("Error while reading sensor %s" % item[1])
        if item[1] == "x1202":
            try:
                with X1202() as x1202_dev1:
                    temp = x1202_dev1.readVoltage()
                    soc = x1202_dev1.readSOC()
                    item[3] = [temp, soc]
            except Exception:
                print ("Error while reading sensor %s" % item[1])
    #FIXME
    #print (ext_sensors)
    return ext_sensors



def check_cpu_temp():
    full_cmd = f"awk '{{printf (\"%.2f\\n\", $1/1000); }}' $(for zone in /sys/class/thermal/thermal_zone*/; do grep -iq \"{config.cpu_thermal_zone}\" \"${{zone}}type\" && echo \"${{zone}}temp\"; done)"    
    try:
        p = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0]
        cpu_temp = p.decode("utf-8").strip().replace(",", ".")
    except Exception:
        cpu_temp = 0

    return cpu_temp


def check_sys_clock_speed():
    full_cmd = "awk '{printf (\"%0.0f\",$1/1000); }' </sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"

    return subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0]


def check_uptime(format):
    full_cmd = "awk '{print int($1"+format+")}' /proc/uptime"

    return int(subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0])


def check_model_name():
   full_cmd = "cat /sys/firmware/devicetree/base/model"
   model_name = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()[0].decode("utf-8")
   if model_name == '':
        full_cmd = "cat /proc/cpuinfo  | grep 'name'| uniq"
        model_name = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()[0].decode("utf-8")
        try:
            model_name = model_name.split(':')[1].replace('\n', '')
        except Exception:
            model_name = 'Unknown'

   return model_name


def check_rpi5_fan_speed():
   full_cmd = "cat /sys/devices/platform/cooling_fan/hwmon/*/fan1_input"
   rpi5_fan_speed = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()[0].decode("utf-8").strip()

   return rpi5_fan_speed


def get_os():
    full_cmd = 'cat /etc/os-release | grep -i pretty_name'
    pretty_name = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()[0].decode("utf-8")
    try:
        pretty_name = pretty_name.split('=')[1].replace('"', '').replace('\n', '')
    except Exception:
        pretty_name = 'Unknown'
        
    return(pretty_name)


def get_manufacturer():
    try:
        if 'Raspberry' not in check_model_name():
            full_cmd = "cat /proc/cpuinfo  | grep 'vendor'| uniq"
            pretty_name = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()[0].decode("utf-8")
            pretty_name = pretty_name.split(':')[1].replace('\n', '')
        else:
            pretty_name = 'Raspberry Pi'
    except Exception:
        pretty_name = 'Unknown'
        
    return(pretty_name)


def check_git_update(script_dir):
    remote_version = update.check_git_version_remote(script_dir)
    if config.version == remote_version:
        git_update = {
                    "installed_ver": config.version,
                    "new_ver": config.version,
                    }
    else:
        git_update = {
                    "installed_ver": config.version,
                    "new_ver": remote_version,
                    }

    return(json.dumps(git_update))


def check_git_version(script_dir):
    full_cmd = "/usr/bin/git -C {} describe --tags `/usr/bin/git -C {} rev-list --tags --max-count=1`".format(script_dir, script_dir)
    git_version = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()[0].decode("utf-8").replace('\n', '')

    return(git_version)


def get_network_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP


def get_mac_address():
    mac_num = uuid.getnode()
    mac = '-'.join((('%012X' % mac_num)[i:i+2] for i in range(0, 12, 2)))
    return mac


def get_apt_updates():
    try:
        subprocess.run(['sudo', 'apt', 'update'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        full_cmd = "apt-get -q -y --ignore-hold --allow-change-held-packages --allow-unauthenticated -s dist-upgrade | /bin/grep ^Inst | wc -l"
        result = subprocess.run(full_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        updates_count = int(result.stdout.strip())
    except Exception as e:
        print(f"Error checking for updates: {e}")
        updates_count = 0

    return updates_count


def get_hwmon_device_name(hwmon_path):
    try:
        with open(os.path.join(hwmon_path, 'name'), 'r') as f:
            return f.read().strip()
    except Exception as e:
        print(f"Error reading name for {hwmon_path}: {e}")
        return None


def get_hwmon_temp(hwmon_path):
    try:
        temp_files = glob.glob(os.path.join(hwmon_path, 'temp*_input'))
        for temp_file in temp_files:
            with open(temp_file, 'r') as tf:
                temp = int(tf.read().strip()) / 1000.0
                return temp
    except Exception as e:
        print(f"Error reading temperature for {hwmon_path}: {e}")
        return None


def check_all_drive_temps():
    drive_temps = {}
    hwmon_devices = glob.glob('/sys/class/hwmon/hwmon*')
    for hwmon in hwmon_devices:
        device_name = get_hwmon_device_name(hwmon)
        if device_name and any(keyword in device_name.lower() for keyword in ['nvme', 'sd']):
            temp = get_hwmon_temp(hwmon)
            if temp is not None:
                drive_temps[device_name] = temp
    return drive_temps


def print_measured_values(cpu_load=0, cpu_temp=0, used_space=0, voltage=0, sys_clock_speed=0, swap=0, memory=0,
                          uptime_days=0, uptime_seconds=0, wifi_signal=0, wifi_signal_dbm=0, rpi5_fan_speed=0, drive_temps=0, rpi_power_status=0, ext_sensors=[]):
    remote_version = update.check_git_version_remote(script_dir)
    output = """:: rpi-mqtt-monitor
   Version: {}

:: Device Information
   Model Name: {}
   Manufacturer: {}
   OS: {}
   Hostname: {}
   IP Address: {}
   MAC Address: {}
""".format(config.version, check_model_name(), get_manufacturer(), get_os(), hostname, get_network_ip(), get_mac_address())

    if args.service:
        output += "   Service Sleep Time: {} seconds\n".format(config.service_sleep_time)
    if config.update:
        output += "   Update Check Interval: {} seconds\n".format(config.update_check_interval)
    output += """
:: Measured values
   CPU Load: {} %
   CPU Temp: {} °C
   Used Space: {} %
   Voltage: {} V
   CPU Clock Speed: {} MHz
   Swap: {} %
   Memory: {} %
   Uptime: {} days
   Wifi Signal: {} %
   Wifi Signal dBm: {}
   RPI5 Fan Speed: {} RPM
   RPI Power Status: {}
   Update: {}
   External Sensors: {}
   """.format(cpu_load, cpu_temp, used_space, voltage, sys_clock_speed, swap, memory, uptime_days, wifi_signal, wifi_signal_dbm, rpi5_fan_speed, rpi_power_status, check_git_update(script_dir), ext_sensors)
    
    drive_temps = check_all_drive_temps()
    if len(drive_temps) > 0:
        for device, temp in drive_temps.items():
            output += f"{device.capitalize()} Temp: {temp:.2f}°C\n"

    output += """\n:: Installation directory: \n   {}

:: Release notes {}: 
{}""".format(script_dir, remote_version, get_release_notes(remote_version).strip())
    print(output)
    

def extract_text(html_string):
    html_string = html.unescape(html_string)
    text = re.sub('<[^<]+?>', '', html_string)

    return text


def get_release_notes(version):
    url = "https://github.com/hjelev/rpi-mqtt-monitor/releases/tag/" + version

    try:
        response = subprocess.run(['curl', '-s', url], capture_output=True)
        release_notes = response.stdout.decode('utf-8').split("What's Changed")[1].split("</div>")[0].replace("</h2>","").split("<p>")[0]
    except Exception:
        release_notes = "No release notes available"

    lines = extract_text(release_notes).split('\n')

    for i in range(len(lines)):
        if lines[i].strip() != "":
            lines[i] = "* " + lines[i]

    release_notes = '\n'.join(lines)

    if len(release_notes) > 255:
        release_notes = release_notes[:250] + " ..."

    release_notes = "### What's Changed" + release_notes

    return release_notes


def config_json(what_config, device="0"):
    model_name = check_model_name()
    manufacturer = get_manufacturer()
    os = get_os()
    data = {
        "state_topic": "",
        "icon": "",
        "name": "",
        "unique_id": "",

        "device": {
            "identifiers": [hostname],
            "manufacturer": 'github.com/hjelev',
            "model": 'RPi MQTT Monitor ' + config.version,
            "name": hostname,
            "sw_version": os,
            "hw_version": model_name + " by " + manufacturer + " IP:" + get_network_ip(),
            "configuration_url": "https://github.com/hjelev/rpi-mqtt-monitor",
            "connections": [["mac", get_mac_address()]]
        }
    }

    data["state_topic"] = config.mqtt_topic_prefix + "/" + hostname + "/" + what_config
    data["unique_id"] = hostname + "_" + what_config
    if what_config == "cpuload":
        data["icon"] = "mdi:speedometer"
        data["name"] = "CPU Usage"
        data["state_class"] = "measurement"
        data["unit_of_measurement"] = "%"
    elif what_config == "cputemp":
        data["icon"] = "hass:thermometer"
        data["name"] = "CPU Temperature"
        data["unit_of_measurement"] = "°C"
        data["device_class"] = "temperature"
        data["state_class"] = "measurement"
    elif what_config == "diskusage":
        data["icon"] = "mdi:harddisk"
        data["name"] = "Disk Usage"
        data["unit_of_measurement"] = "%"
        data["state_class"] = "measurement"
    elif what_config == "voltage":
        data["icon"] = "mdi:flash"
        data["name"] = "CPU Voltage"
        data["unit_of_measurement"] = "V"
        data["device_class"] = "voltage"
        data["state_class"] = "measurement"
    elif what_config == "swap":
        data["icon"] = "mdi:harddisk"
        data["name"] = "Disk Swap"
        data["unit_of_measurement"] = "%"
        data["state_class"] = "measurement"
    elif what_config == "memory":
        data["icon"] = "mdi:memory"
        data["name"] = "Memory Usage"
        data["unit_of_measurement"] = "%"
        data["state_class"] = "measurement"
    elif what_config == "sys_clock_speed":
        data["icon"] = "mdi:speedometer"
        data["name"] = "CPU Clock Speed"
        data["unit_of_measurement"] = "MHz"
        data["device_class"] = "frequency"
        data["state_class"] = "measurement"
    elif what_config == "uptime_days":
        data["icon"] = "mdi:calendar"
        data["name"] = "Uptime"
        data["unit_of_measurement"] = "d"
        data["device_class"] = "duration"
        data["state_class"] = "total_increasing"
    elif what_config == "uptime_seconds":
        data["icon"] = "mdi:timer-outline"
        data["name"] = "Uptime"
        data["unit_of_measurement"] = "s"
        data["device_class"] = "duration"
        data["state_class"] = "total_increasing"
    elif what_config == "wifi_signal":
        data["icon"] = "mdi:wifi"
        data["name"] = "Wifi Signal"
        data["unit_of_measurement"] = "%"
        data["state_class"] = "measurement"
    elif what_config == "wifi_signal_dbm":
        data["icon"] = "mdi:wifi"
        data["name"] = "Wifi Signal"
        data["unit_of_measurement"] = "dBm"
        data["device_class"] = "signal_strength"
        data["state_class"] = "measurement"
    elif what_config == "rpi5_fan_speed":
        data["icon"] = "mdi:fan"
        data["name"] = "Fan Speed"
        data["unit_of_measurement"] = "RPM"
        data["state_class"] = "measurement"
    elif what_config == "status":
        data["icon"] = "mdi:lan-connect"
        data["name"] = "Status"
        data["value_template"] = "{{ 'online' if value == '1' else 'offline' }}"
    elif what_config == "git_update":
        data["icon"] = "mdi:git"
        data["name"] = "RPi MQTT Monitor"
        data["title"] = "Device Update"
        data["device_class"] = "update"
        data["state_class"] = "measurement"
        data["value_template"] = "{{ 'ON' if value_json.installed_ver != value_json.new_ver else 'OFF' }}"
    elif what_config == "update":
        version = update.check_git_version_remote(script_dir)
        data["icon"] = "mdi:update"
        data["name"] = "RPi MQTT Monitor"
        data["title"] = "New Version"
        data["state_topic"] = config.mqtt_topic_prefix + "/" + hostname + "/" + "git_update"
        data["value_template"] = "{{ {'installed_version': value_json.installed_ver, 'latest_version': value_json.new_ver } | to_json }}"
        data["device_class"] = "firmware"
        data["command_topic"] = config.mqtt_discovery_prefix + "/update/" + hostname + "/command"
        data["payload_install"] = "install"
        data['release_url'] = "https://github.com/hjelev/rpi-mqtt-monitor/releases/tag/" + version
        data['entity_picture'] = "https://masoko.net/rpi-mqtt-monitor.png"
        data['release_summary'] = get_release_notes(version)
    elif what_config == "restart_button":
        data["icon"] = "mdi:restart"
        data["name"] = "System Restart"
        data["command_topic"] = config.mqtt_discovery_prefix + "/update/" + hostname + "/command"
        data["payload_press"] = "restart"
        data["device_class"] = "restart"
    elif what_config == "shutdown_button":
        data["icon"] = "mdi:power"
        data["name"] = "System Shutdown"
        data["command_topic"] = config.mqtt_discovery_prefix + "/update/" + hostname + "/command"
        data["payload_press"] = "shutdown"
        data["device_class"] = "restart"
    elif what_config == "display_on":
        data["icon"] = "mdi:monitor"
        data["name"] = "Monitor ON"
        data["command_topic"] = config.mqtt_discovery_prefix + "/update/" + hostname + "/command"
        data["payload_press"] = "display_on"
        data["device_class"] = "restart"
    elif what_config == "display_off":
        data["icon"] = "mdi:monitor"
        data["name"] = "Monitor OFF"
        data["command_topic"] = config.mqtt_discovery_prefix + "/update/" + hostname + "/command"
        data["payload_press"] = "display_off"
        data["device_class"] = "restart"
    elif what_config == device + "_temp":
        data["icon"] = "hass:thermometer"
        data["name"] = device + " Temperature"
        data["unit_of_measurement"] = "°C"
        data["device_class"] = "temperature"
        data["state_class"] = "measurement"
    elif what_config == "rpi_power_status":
        data["icon"] = "mdi:flash"
        data["name"] = "RPi Power Status"  
    elif what_config == "apt_updates":
        data["icon"] = "mdi:update"
        data["name"] = "APT Updates"
    elif what_config == "ds18b20_status":
        data["icon"] = "hass:thermometer"
        data["name"] = device + " Temperature"
        data["unit_of_measurement"] = "°C"
        data["device_class"] = "temperature"
        data["state_class"] = "measurement"
        # we define again the state topic in order to get a unique state topic if we have two sensors of the same type
        data["state_topic"] = config.mqtt_topic_prefix + "/" + hostname + "/" + what_config + "_" + device
        data["unique_id"] = hostname + "_" + what_config + "_" + device
    elif what_config == "sht21_temp_status":
        data["icon"] = "hass:thermometer"
        data["name"] = device + " Temperature"
        data["unit_of_measurement"] = "°C"
        data["device_class"] = "temperature"
        data["state_class"] = "measurement"
        # we define again the state topic in order to get a unique state topic if we have two sensors of the same type
        data["state_topic"] = config.mqtt_topic_prefix + "/" + hostname + "/" + what_config + "_" + device
        data["unique_id"] = hostname + "_" + what_config + "_" + device
    elif what_config == "sht21_hum_status":
        data["icon"] = "mdi:water-percent"
        data["name"] = device + " Humidity"
        data["unit_of_measurement"] = "%"
        data["device_class"] = "temperature"
        data["state_class"] = "measurement"
        # we define again the state topic in order to get a unique state topic if we have two sensors of the same type
        data["state_topic"] = config.mqtt_topic_prefix + "/" + hostname + "/" + what_config + "_" + device
        data["unique_id"] = hostname + "_" + what_config + "_" + device
    elif what_config == "x1202_temp_status":
        data["icon"] = "hass:thermometer"
        data["name"] = device + " Temperature"
        data["unit_of_measurement"] = "°C"
        data["device_class"] = "temperature"
        data["state_class"] = "measurement"
        # we define again the state topic in order to get a unique state topic if we have two sensors of the same type
        data["state_topic"] = config.mqtt_topic_prefix + "/" + hostname + "/" + what_config + "_" + device
        data["unique_id"] = hostname + "_" + what_config + "_" + device
    elif what_config == "x1202_soc_status":
        data["icon"] = "hass:battery"
        data["name"] = device + " Voltage"
        data["unit_of_measurement"] = "V"
        data["device_class"] = "voltage"
        data["state_class"] = "measurement"
        # we define again the state topic in order to get a unique state topic if we have two sensors of the same type
        data["state_topic"] = config.mqtt_topic_prefix + "/" + hostname + "/" + what_config + "_" + device
        data["unique_id"] = hostname + "_" + what_config + "_" + device
    
    else:
        return ""
    # Return our built discovery config
    return json.dumps(data)


def create_mqtt_client():

    def on_log(client, userdata, level, buf):
        if level == paho.MQTT_LOG_ERR:
            print("MQTT error: ", buf)


    def on_connect(client, userdata, flags, rc):
        if rc != 0:
            print("Error: Unable to connect to MQTT broker, return code:", rc)


    client = paho.Client(client_id="rpi-mqtt-monitor-" + hostname + str(int(time.time())))
    client.username_pw_set(config.mqtt_user, config.mqtt_password)
    client.on_log = on_log
    client.on_connect = on_connect
    try:
        client.connect(config.mqtt_host, int(config.mqtt_port))
    except Exception as e:
        print("Error connecting to MQTT broker:", e)
        return None
    return client


def publish_update_status_to_mqtt(git_update, apt_updates):

    client = create_mqtt_client()
    if client is None:
        print("Error: Unable to connect to MQTT broker")
        return

    client.loop_start()
    if config.git_update:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/binary_sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_git_update/config",
                           config_json('git_update'), qos=config.qos)
        client.publish(config.mqtt_topic_prefix + "/" + hostname + "/git_update", git_update, qos=1, retain=config.retain)

    if config.update:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/update/" + hostname + "/config",
                           config_json('update'), qos=1)

    if config.apt_updates:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_apt_updates/config",
                           config_json('apt_updates'), qos=config.qos)
        client.publish(config.mqtt_topic_prefix + "/" + hostname + "/apt_updates", apt_updates, qos=config.qos, retain=config.retain)


    # Wait for all messages to be delivered
    while len(client._out_messages) > 0:
        time.sleep(0.1)
        client.loop()

    client.loop_stop()
    client.disconnect()


def publish_to_mqtt(cpu_load=0, cpu_temp=0, used_space=0, voltage=0, sys_clock_speed=0, swap=0, memory=0,
                    uptime_days=0, uptime_seconds=0, wifi_signal=0, wifi_signal_dbm=0, rpi5_fan_speed=0, drive_temps=0, rpi_power_status=0, ext_sensors=[]):
    client = create_mqtt_client()
    if client is None:
        return

    client.loop_start()

    if config.cpu_load:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_cpuload/config",
                           config_json('cpuload'), qos=config.qos)
        client.publish(config.mqtt_topic_prefix + "/" + hostname + "/cpuload", cpu_load, qos=config.qos, retain=config.retain)
    if config.cpu_temp:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_cputemp/config",
                           config_json('cputemp'), qos=config.qos)
        client.publish(config.mqtt_topic_prefix + "/" + hostname + "/cputemp", cpu_temp, qos=config.qos, retain=config.retain)
    if config.used_space:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_diskusage/config",
                           config_json('diskusage'), qos=config.qos)
        client.publish(config.mqtt_topic_prefix + "/" + hostname + "/diskusage", used_space, qos=config.qos, retain=config.retain)
    if config.voltage:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_voltage/config",
                           config_json('voltage'), qos=config.qos)
        client.publish(config.mqtt_topic_prefix + "/" + hostname + "/voltage", voltage, qos=config.qos, retain=config.retain)
    if config.swap:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_swap/config",
                           config_json('swap'), qos=config.qos)
        client.publish(config.mqtt_topic_prefix + "/" + hostname + "/swap", swap, qos=config.qos, retain=config.retain)
    if config.memory:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_memory/config",
                           config_json('memory'), qos=config.qos)
        client.publish(config.mqtt_topic_prefix + "/" + hostname + "/memory", memory, qos=config.qos, retain=config.retain)
    if config.sys_clock_speed:
        if config.discovery_messages:
            client.publish(
                config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_sys_clock_speed/config",
                config_json('sys_clock_speed'), qos=config.qos)
        client.publish(config.mqtt_topic_prefix + "/" + hostname + "/sys_clock_speed", sys_clock_speed, qos=config.qos, retain=config.retain)
    if config.uptime:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_uptime_days/config",
                           config_json('uptime_days'), qos=config.qos)
        client.publish(config.mqtt_topic_prefix + "/" + hostname + "/uptime_days", uptime_days, qos=config.qos, retain=config.retain)
    if config.uptime_seconds:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_uptime_seconds/config",
                           config_json('uptime_seconds'), qos=config.qos)
        client.publish(config.mqtt_topic_prefix + "/" + hostname + "/uptime_seconds", uptime_seconds, qos=config.qos, retain=config.retain)
    if config.wifi_signal:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_wifi_signal/config",
                           config_json('wifi_signal'), qos=config.qos)
        client.publish(config.mqtt_topic_prefix + "/" + hostname + "/wifi_signal", wifi_signal, qos=config.qos, retain=config.retain)
    if config.wifi_signal_dbm:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_wifi_signal_dbm/config",
                           config_json('wifi_signal_dbm'), qos=config.qos)
        client.publish(config.mqtt_topic_prefix + "/" + hostname + "/wifi_signal_dbm", wifi_signal_dbm, qos=config.qos, retain=config.retain)
    if config.rpi5_fan_speed:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_rpi5_fan_speed/config",
                           config_json('rpi5_fan_speed'), qos=config.qos)
        client.publish(config.mqtt_topic_prefix + "/" + hostname + "/rpi5_fan_speed", rpi5_fan_speed, qos=config.qos, retain=config.retain)
    if config.restart_button:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/button/" + config.mqtt_topic_prefix + "/" + hostname + "_restart/config",
                           config_json('restart_button'), qos=config.qos)
    if config.shutdown_button:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/button/" + config.mqtt_topic_prefix + "/" + hostname + "_shutdown/config",
                           config_json('shutdown_button'), qos=config.qos)
    if config.display_control:
        if config.discovery_messages:
            client.publish(config.mqtt_discovery_prefix + "/button/" + config.mqtt_topic_prefix + "/" + hostname + "_display_on/config",
                           config_json('display_on'), qos=config.qos)
            client.publish(config.mqtt_discovery_prefix + "/button/" + config.mqtt_topic_prefix + "/" + hostname + "_display_off/config",
                           config_json('display_off'), qos=config.qos)
    if config.drive_temps:
        for device, temp in drive_temps.items():
            if config.discovery_messages:
                client.publish(config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_" + device + "_temp/config",
                           config_json(device + "_temp", device), qos=config.qos)
            client.publish(config.mqtt_topic_prefix + "/" + hostname + "/" + device + "_temp", temp, qos=config.qos, retain=config.retain)
    if config.rpi_power_status:
        if config.discovery_messages:
            client.publish(
                config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_rpi_power_status/config",
                config_json('rpi_power_status'), qos=config.qos)
        client.publish(config.mqtt_topic_prefix + "/" + hostname + "/rpi_power_status", rpi_power_status, qos=config.qos, retain=config.retain)
    if config.ext_sensors:
        # we loop through all sensors
        for item in ext_sensors:
            # item[0] = name
            # item[1] = sensor_type
            # item[2] = ID
            # item[3] = value, like temperature or humidity
            if item[1] == "ds18b20":
                if config.discovery_messages:
                    client.publish(
                        config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_" + item[0] + "_status/config",
                        config_json('ds18b20_status', device=item[0]), qos=config.qos)
                client.publish(config.mqtt_topic_prefix + "/" + hostname + "/" + "ds18b20_status_" + item[0], item[3], qos=config.qos, retain=config.retain)
            if item[1] == "sht21":
                if config.discovery_messages:
                    # temperature
                    client.publish(
                        config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_" + item[0] + "_temp_status/config",
                        config_json('sht21_temp_status', device=item[0]), qos=config.qos)
                    # humidity
                    client.publish(
                        config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_" + item[0] + "_hum_status/config",
                        config_json('sht21_hum_status', device=item[0]), qos=config.qos)
                # temperature
                client.publish(config.mqtt_topic_prefix + "/" + hostname + "/" + "sht21_temp_status_" + item[0], item[3][0], qos=config.qos, retain=config.retain)
                # humidity
                client.publish(config.mqtt_topic_prefix + "/" + hostname + "/" + "sht21_hum_status_" + item[0], item[3][1], qos=config.qos, retain=config.retain)
            if item[1] == "x1202":
                if config.discovery_messages:
                    # temperature
                    client.publish(
                        config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_" + item[0] + "_temp_status/config",
                        config_json('x1202_temp_status', device=item[0]), qos=config.qos)
                    # humidity
                    client.publish(
                        config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_" + item[0] + "_soc_status/config",
                        config_json('x1202_soc_status', device=item[0]), qos=config.qos)
                # temperature
                client.publish(config.mqtt_topic_prefix + "/" + hostname + "/" + "x1202_temp_status_" + item[0], item[3][0], qos=config.qos, retain=config.retain)
                # humidity
                client.publish(config.mqtt_topic_prefix + "/" + hostname + "/" + "x1202_soc_status_" + item[0], item[3][1], qos=config.qos, retain=config.retain)
                
                

    status_sensor_topic = config.mqtt_discovery_prefix + "/sensor/" + config.mqtt_topic_prefix + "/" + hostname + "_status/config"
    client.publish(status_sensor_topic, config_json('status'), qos=config.qos)
    client.publish(config.mqtt_topic_prefix + "/" + hostname + "/status", "1", qos=config.qos, retain=config.retain)
    
    while len(client._out_messages) > 0:
        time.sleep(0.1)
        client.loop()

    client.loop_stop()
    client.disconnect()


def bulk_publish_to_mqtt(cpu_load=0, cpu_temp=0, used_space=0, voltage=0, sys_clock_speed=0, swap=0, memory=0,
                         uptime_days=0, uptime_seconds=0, wifi_signal=0, wifi_signal_dbm=0, rpi5_fan_speed=0, git_update=0, rpi_power_status="0", ext_sensors=[]):
    # compose the CSV message containing the measured values

    values = (cpu_load, cpu_temp, used_space, voltage, int(sys_clock_speed), swap, memory, uptime_days, uptime_seconds, wifi_signal, wifi_signal_dbm, rpi5_fan_speed, git_update, rpi_power_status) + tuple(sensor[3] for sensor in ext_sensors)
    values = str(values)[1:-1]

    client = create_mqtt_client()
    if client is None:
        return

    client.loop_start()
    client.publish(config.mqtt_topic_prefix + "/" + hostname, values, qos=config.qos, retain=config.retain)

    while len(client._out_messages) > 0:
        time.sleep(0.1)
        client.loop()

    client.loop_stop()
    client.disconnect()


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('--display', '-d', action='store_true', help='display values on screen', default=False)
    parser.add_argument('--service', '-s', action='store_true', help='run script as a service, sleep interval is configurable in config.py', default=False)
    parser.add_argument('--version', '-v', action='store_true', help='display installed version and exit', default=False)
    parser.add_argument('--update',  '-u', action='store_true', help='update script and config then exit', default=False)
    parser.add_argument('--hass',    '-H', action='store_true', help='display Home assistant wake on lan configuration', default=False)
    args = parser.parse_args()

    if args.update:
        version = update.check_git_version_remote(script_dir).strip()
        git_update = check_git_update(script_dir)

        if git_update == 'on':
            git_update = True
        else:
            git_update = False

        update.do_update(script_dir, version, git_update)

        exit()

    if args.version:
        installed_version = config.version
        latest_versino = update.check_git_version_remote(script_dir).strip()
        print("Installed version: " + installed_version)
        print("Latest version: " + latest_versino)
        if installed_version != latest_versino:
            print("Update available")
        else:
            print("No update available")
        exit()

    if args.hass:
        hass_config = """Add this to your Home Assistant switches.yaml file: 

  - platform: wake_on_lan
    mac: "{}"
    host: "{}"
    name: "{}-switch"
    turn_off:
      service: mqtt.publish
      data:
        topic: config.mqtt_discovery_prefix + "/update/{}/command"
        payload: "shutdown"
    """.format(get_mac_address(), get_network_ip(), hostname, hostname )
        print(hass_config)
        exit()

    return args


def collect_monitored_values():
    cpu_load = cpu_temp = used_space = voltage = sys_clock_speed = swap = memory = uptime_seconds = uptime_days = wifi_signal = wifi_signal_dbm = rpi5_fan_speed = drive_temps = rpi_power_status = ext_sensors = False

    if config.cpu_load:
        cpu_load = check_cpu_load()
    if config.cpu_temp:
        cpu_temp = check_cpu_temp()
    if config.used_space:
        used_space = check_used_space(config.used_space_path)
    if config.voltage:
        voltage = check_voltage()
    if config.sys_clock_speed:
        sys_clock_speed = check_sys_clock_speed()
    if config.swap:
        swap = check_swap()
    if config.memory:
        memory = check_memory()
    if config.uptime:
        uptime_days = check_uptime('/3600/24')
    if config.uptime_seconds:
        uptime_seconds = check_uptime('')
    if config.wifi_signal:
        wifi_signal = check_wifi_signal('')
    if config.wifi_signal_dbm:
        wifi_signal_dbm = check_wifi_signal('dbm')
    if config.rpi5_fan_speed:
        rpi5_fan_speed = check_rpi5_fan_speed()
    if config.drive_temps:
        drive_temps = check_all_drive_temps()
    if config.rpi_power_status:
        rpi_power_status = check_rpi_power_status()
    if config.ext_sensors:
        ext_sensors = read_ext_sensors()

    return cpu_load, cpu_temp, used_space, voltage, sys_clock_speed, swap, memory, uptime_days, uptime_seconds, wifi_signal, wifi_signal_dbm, rpi5_fan_speed, drive_temps, rpi_power_status, ext_sensors


def gather_and_send_info():
    while not stop_event.is_set():
        cpu_load, cpu_temp, used_space, voltage, sys_clock_speed, swap, memory, uptime_days, uptime_seconds, wifi_signal, wifi_signal_dbm, rpi5_fan_speed, drive_temps, rpi_power_status, ext_sensors= collect_monitored_values()

        if hasattr(config, 'random_delay'):
            time.sleep(config.random_delay)

        if args.display:
            print_measured_values(cpu_load, cpu_temp, used_space, voltage, sys_clock_speed, swap, memory, uptime_days, uptime_seconds, wifi_signal, wifi_signal_dbm, rpi5_fan_speed, drive_temps, rpi_power_status, ext_sensors)

        # write some output to a file
        if config.output_filename:
            # the only options are "a" for append or "w" for (over)write
            # check if one of this options is defined
            if config.output_mode not in ["a", "w"]:
                print("Error, output_type not known. Default w is set.")
                config.output_type = "w"
            try:
                # open the text file
                output_file = open(config.output_filename, config.output_mode)
                # read what should be written into the textfile
                # we need to define this is a function, otherwise the values are not updated and default values are taken
                output_content = config.get_content_outputfile()
                output_file.write(output_content)
                output_file.close()
            except Exception as e:
                print("Error writing to output file:", e)

        if hasattr(config, 'group_messages') and config.group_messages:
            bulk_publish_to_mqtt(cpu_load, cpu_temp, used_space, voltage, sys_clock_speed, swap, memory, uptime_days, uptime_seconds, wifi_signal, wifi_signal_dbm, rpi5_fan_speed, drive_temps, rpi_power_status, ext_sensors)
        else:
            publish_to_mqtt(cpu_load, cpu_temp, used_space, voltage, sys_clock_speed, swap, memory, uptime_days, uptime_seconds, wifi_signal, wifi_signal_dbm, rpi5_fan_speed, drive_temps, rpi_power_status, ext_sensors)

        if not args.service:
            break
        # Break the sleep into 1-second intervals and check stop_event after each interval
        for _ in range(config.service_sleep_time):
            if stop_event.is_set():
                break
            time.sleep(1)


def update_status():
    while not stop_event.is_set():
        git_update = check_git_update(script_dir)
        apt_updates = get_apt_updates()
        publish_update_status_to_mqtt(git_update, apt_updates)
        stop_event.wait(config.update_check_interval)
        if stop_event.is_set():
            break


def on_message(client, userdata, msg):
    global exit_flag, thread1, thread2
    print("Received message: ", msg.payload.decode())
    if msg.payload.decode() == "install":
        def update_and_exit():
            version = update.check_git_version_remote(script_dir).strip()
            update.do_update(script_dir, version, git_update=True, config_update=True)
            print("Update completed. Stopping MQTT client loop...")
            client.loop_stop()  # Stop the MQTT client loop
            print("Setting exit flag...")
            exit_flag = True
            stop_event.set()  # Signal the threads to stop
            if thread1 is not None:
                thread1.join()  # Wait for thread1 to finish
            if thread2 is not None:
                thread2.join()  # Wait for thread2 to finish
            os._exit(0)  # Exit the script immediately

        update_thread = threading.Thread(target=update_and_exit)
        update_thread.start()
    elif msg.payload.decode() == "restart":
        print("Restarting the system...")
        os.system("sudo reboot")
    elif msg.payload.decode() == "shutdown":
        print("Shutting down the system...")
        os.system("sudo shutdown now")
    elif msg.payload.decode() == "display_off":
        print("Turn off display")
        os.system('su -l {} -c "xset -display :0 dpms force off"'.format(config.os_user))
    elif msg.payload.decode() == "display_on":
        print("Turn on display")
        os.system('su -l {} -c "xset -display :0 dpms force on"'.format(config.os_user))

exit_flag = False
stop_event = threading.Event()
script_dir = os.path.dirname(os.path.realpath(__file__))
# get device host name - used in mqtt topic
# and adhere to the allowed character set
hostname = re.sub(r'[^a-zA-Z0-9_-]', '_', socket.gethostname())

if __name__ == '__main__':
    args = parse_arguments();
    if args.service:
        client = paho.Client()
        client.username_pw_set(config.mqtt_user, config.mqtt_password)
        client.on_message = on_message
        # set will_set to send a message when the client disconnects
        client.will_set(config.mqtt_topic_prefix + "/" + hostname + "/status", "0", qos=config.qos, retain=config.retain)
        try:
            client.connect(config.mqtt_host, int(config.mqtt_port))
        except Exception as e:
            print("Error connecting to MQTT broker:", e)
            sys.exit(1)

        client.subscribe(config.mqtt_discovery_prefix + "/update/" + hostname + "/command")
        print("Listening to topic : " + config.mqtt_discovery_prefix + "/update/" + hostname + "/command")
        client.loop_start()
        thread1 = threading.Thread(target=gather_and_send_info)
        thread1.daemon = True  # Set thread1 as a daemon thread
        thread1.start()

        if config.update:
            thread2 = threading.Thread(target=update_status)
            thread2.daemon = True  # Set thread2 as a daemon thread
            thread2.start()

        try:
            while True:
                time.sleep(1)  # Check the exit flag every second
        except KeyboardInterrupt:
            print(" Ctrl+C pressed. Setting exit flag...")
            client.loop_stop()
            exit_flag = True
            stop_event.set()  # Signal the threads to stop
            sys.exit(0)  # Exit the script
    else:
        gather_and_send_info()
