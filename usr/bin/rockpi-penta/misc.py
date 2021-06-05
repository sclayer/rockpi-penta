#!/usr/bin/env python3
import re
import os
import sys
import time
import mraa  # pylint: disable=import-error
import shutil
import subprocess
import multiprocessing as mp
from configparser import ConfigParser
from collections import defaultdict, OrderedDict

cmds = {
    'blk': "lsblk | awk '{print $1}'",
    #'up': "echo Uptime: `uptime | sed 's/.*up \\([^,]*\\), .*/\\1/'`",
    'up': "echo Up: $(uptime -p | sed 's/ years,/y/g;s/ year,/y/g;s/ months,/m/g;s/ month,/m/g;s/ weeks,/w/g;s/ week,/w/g;s/ days,/d/g;s/ day,/d/g;s/ hours,/h/g;s/ hour,/h/g;s/ minutes/m/g;s/ minute/m/g' | cut -d ' ' -f2-)",
    'temp': "cat /sys/class/thermal/thermal_zone0/temp",
    'ip': "hostname -I | awk '{printf \"IP %s\", $1}'",
    'cpu': "uptime | awk '{printf \"CPU Load: %.2f\", $(NF-2)}'",
    'men': "free -m | awk 'NR==2{printf \"Mem: %s/%sMB\", $3,$2}'",
    'disk': "df -h | awk '$NF==\"/\"{printf \"Disk: %d/%dGB %s\", $3,$2,$5}'"
}

lv2dc = OrderedDict({
    'lv7': 0, 
    'lv6': 0.12, 
    'lv5': 0.25, 
    'lv4': 0.37, 
    'lv3': 0.5, 
    'lv2': 0.62, 
    'lv1': 0.75, 
    'lv0': 1
    })


def set_mode(pin, mode=1):
    try:
        pin = mraa.Gpio(pin)
        pin.dir(mraa.DIR_OUT)
        pin.write(mode)
    except Exception as ex:
        print(ex)


def check_output(cmd):
    return subprocess.check_output(cmd, shell=True).decode().strip()


def check_call(cmd):
    return subprocess.check_call(cmd, shell=True)


def get_blk():
    conf['disk'] = [x for x in check_output(cmds['blk']).strip().split('\n') if x.startswith('sd')]


def get_info(s):
    return check_output(cmds[s])


def get_cpu_temp():
    t = float(get_info('temp')) / 1000
    if conf['oled']['f-temp']:
        temp = "CPU Temp: {:.0f}°F".format(t * 1.8 + 32)
    else:
        temp = "CPU Temp: {:.1f}°C".format(t)
    return temp


def read_conf():
    conf = defaultdict(dict)

    try:
        cfg = ConfigParser()
        cfg.read('/etc/rockpi-penta.conf')
        # fan
        # Reverse compativility for old configs
        # If none of the new value are founf in the cfg
        if bool(set(['lv4', 'lv5', 'lv6', 'lv7']) & set(cfg['fan'].keys())) == False:
            conf['fan']['lv0'] = cfg.getfloat('fan','lv0')
            conf['fan']['lv1'] = cfg.getfloat('fan','lv0')
            conf['fan']['lv2'] = cfg.getfloat('fan','lv1')
            conf['fan']['lv3'] = cfg.getfloat('fan','lv1')
            conf['fan']['lv4'] = cfg.getfloat('fan','lv2')
            conf['fan']['lv5'] = cfg.getfloat('fan','lv2')
            conf['fan']['lv6'] = cfg.getfloat('fan','lv3')
            conf['fan']['lv7'] = cfg.getfloat('fan','lv3')
        else:
            conf['fan']['lv0'] = cfg.getfloat('fan','lv0')
            conf['fan']['lv1'] = cfg.getfloat('fan','lv1')
            conf['fan']['lv2'] = cfg.getfloat('fan','lv2')
            conf['fan']['lv3'] = cfg.getfloat('fan','lv3')
            conf['fan']['lv4'] = cfg.getfloat('fan','lv4')
            conf['fan']['lv5'] = cfg.getfloat('fan','lv5')
            conf['fan']['lv6'] = cfg.getfloat('fan','lv6')            
            conf['fan']['lv7'] = cfg.getfloat('fan','lv7')
        # key
        conf['key']['click'] = cfg.get('key', 'click')
        conf['key']['twice'] = cfg.get('key', 'twice')
        conf['key']['press'] = cfg.get('key', 'press')
        # time
        conf['time']['twice'] = cfg.getfloat('time', 'twice')
        conf['time']['press'] = cfg.getfloat('time', 'press')
        # other
        conf['slider']['auto'] = cfg.getboolean('slider', 'auto')
        conf['slider']['time'] = cfg.getfloat('slider', 'time')
        conf['oled']['rotate'] = cfg.getboolean('oled', 'rotate')
        conf['oled']['f-temp'] = cfg.getboolean('oled', 'f-temp')
        # network
        conf['network']['interfaces'] = cfg.get('network','interfaces').split('|')
    except Exception:
        # fan
        conf['fan']['lv0'] = 35
        conf['fan']['lv1'] = 37
        conf['fan']['lv2'] = 40
        conf['fan']['lv3'] = 42
        conf['fan']['lv4'] = 44
        conf['fan']['lv5'] = 46
        conf['fan']['lv6'] = 48
        conf['fan']['lv7'] = 50
        # key
        conf['key']['click'] = 'slider'
        conf['key']['twice'] = 'switch'
        conf['key']['press'] = 'none'
        # time
        conf['time']['twice'] = 0.7  # second
        conf['time']['press'] = 1.8
        # other
        conf['slider']['auto'] = True
        conf['slider']['time'] = 10  # second
        conf['oled']['rotate'] = False
        conf['oled']['f-temp'] = False
        # network
        conf['network']['interfaces'] = []

    return conf


def read_key(pattern, size):
    s = ''
    pin11 = mraa.Gpio(11)
    pin11.dir(mraa.DIR_IN)

    while True:
        s = s[-size:] + str(pin11.read())
        for t, p in pattern.items():
            if p.match(s):
                return t
        time.sleep(0.1)


def watch_key(q=None):
    size = int(conf['time']['press'] * 10)
    wait = int(conf['time']['twice'] * 10)
    pattern = {
        'click': re.compile(r'1+0+1{%d,}' % wait),
        'twice': re.compile(r'1+0+1+0+1{3,}'),
        'press': re.compile(r'1+0{%d,}' % size),
    }

    while True:
        q.put(read_key(pattern, size))

def get_interface_list():
    if len(conf['network']['interfaces']) == 1 and conf['network']['interfaces'][0] == '':
        return []

    if len(conf['network']['interfaces']) == 1 and conf['network']['interfaces'][0] == 'auto':
        interfaces = []
        cmd = "ip -o link show | awk '{print $2,$9}'"
        list = check_output(cmd).split('\n')
        for x in list:
            name_status = x.split(': ')
            if "UP" in name_status[1]:
                interfaces.append(name_status[0])

        interfaces.sort()

    else:
        interfaces = conf['network']['interfaces']

    return interfaces


def get_interface_rx_info(interface):
    cmd = "R1=$(cat /sys/class/net/" + interface + "/statistics/rx_bytes); sleep 1; R2=$(cat /sys/class/net/" + interface + "/statistics/rx_bytes); echo | awk -v r1=$R1 -v r2=$R2 '{printf \"rx: %.5f MB/s\", (r2 - r1) / 1024 / 1024}';"
    output = check_output(cmd)
    return output


def get_interface_tx_info(interface):
    cmd = "T1=$(cat /sys/class/net/" + interface + "/statistics/tx_bytes); sleep 1; T2=$(cat /sys/class/net/" + interface + "/statistics/tx_bytes); echo | awk -v t1=$T1 -v t2=$T2 '{printf \"tx: %.5f MB/s\", (t2 - t1) / 1024 / 1024}';"
    output = check_output(cmd)
    return output

def get_disk_info(cache={}):
    if not cache.get('time') or time.time() - cache['time'] > 30:
        info = {}
        cmd = "df -h | awk '$NF==\"/\"{printf \"%s\", $5}'"
        info['root'] = check_output(cmd)
        for x in conf['disk']:
            cmd = "df -Bg | awk '$1==\"/dev/{}\" {{printf \"%s\", $5}}'".format(x)
            info[x] = check_output(cmd)
        cache['info'] = list(zip(*info.items()))
        cache['time'] = time.time()

    return cache['info']


def slider_next(pages):
    conf['idx'].value += 1
    return pages[conf['idx'].value % len(pages)]


def slider_sleep():
    time.sleep(conf['slider']['time'])


def fan_temp2dc(t):
    for lv, dc in lv2dc.items():
        if t >= conf['fan'][lv]:
            return dc
    return 0.999


def fan_switch():
    conf['run'].value = not(conf['run'].value)


def get_func(key):
    return conf['key'].get(key, 'none')


def open_pwm_i2c():
    def replace(filename, raw_str, new_str):
        with open(filename, 'r') as f:
            content = f.read()

        if raw_str in content:
            shutil.move(filename, filename + '.bak')
            content = content.replace(raw_str, new_str)

            with open(filename, 'w') as f:
                f.write(content)

    replace('/boot/hw_intfc.conf', 'intfc:pwm0=off', 'intfc:pwm0=on')
    replace('/boot/hw_intfc.conf', 'intfc:pwm1=off', 'intfc:pwm1=on')
    replace('/boot/hw_intfc.conf', 'intfc:i2c7=off', 'intfc:i2c7=on')


conf = {'disk': [], 'idx': mp.Value('d', -1), 'run': mp.Value('d', 1)}
conf.update(read_conf())


if __name__ == '__main__':
    if sys.argv[-1] == 'open_pwm_i2c':
        open_pwm_i2c()
