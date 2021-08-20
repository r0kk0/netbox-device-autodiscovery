import re
from typing import Optional, Iterable, Union

import requests
from lxml import html
from pysnmp.hlapi import SnmpEngine, CommunityData, UdpTransportTarget, ContextData, ObjectType, ObjectIdentity, getCmd

from shared_objects import Device


def safe_http_get(url, params: dict = None, timeout=3, **kwargs) -> Optional[requests.Response]:
    try:
        r = requests.get(url, params=params, timeout=timeout, verify=False, **kwargs)
    except requests.exceptions.RequestException:
        return
    else:
        return r


def str_contains(string: str, substrings: Iterable[str]) -> bool:
    """Checks whether the string contains all the specified substrings"""
    for substring in substrings:
        if substring not in string:
            return False
    return True


def recognize_by_http(ip: str, port=80, http_timeout=3) -> Optional[Device]:
    """Recognize device with a web interface: printers, routers, etc."""
    base_url = f'http://{ip}:{port}'
    r = safe_http_get(f'{base_url}/', timeout=http_timeout)
    if not r:
        return

    # Recognize device by the Server response header
    if 'Server' in r.headers:
        server_header = r.headers['Server']
        if match := re.search(r'HP (DesignJet \w+) MFP', server_header):
            return Device('HP', match.group(1), 'MFP', None)

    # Recognize device by the response body
    if 'SeWoo Ethernet IP Config' in r.text:
        return Device('SeWoo', 'printer', 'Printer', None)
    elif (('TP-Link Corporation Limited.' in r.text) or ('TP-Link Technologies Co., Ltd.' in r.text)) and \
            str_contains(r.text, ('var g_Lan', 'var g_year')):
        return Device('TP-Link', 'switch', 'Switch', None)
    elif ('Zebra Technologies' in r.text) and (match := re.search(r'ZTC ([\w-]+)', r.text)):
        return Device('Zebra', match.group(1), 'Printer', None)
    elif 'KYOCERA MITA' in r.text:
        manufacturer = 'KYOCERA'
        if r := safe_http_get(
                f'{base_url}/js/jssrc/model/startwlm/Start_Wlm.model.htm',
                headers={'Referer': f'{base_url}/startwlm/Start_Wlm.htm'}, cookies={'rtl': '0'}):
            if match := re.search(r"f_getPrinterModel = 'ECOSYS (\w+)';", r.text):
                return Device(manufacturer, f'ECOSYS {match.group(1)}', 'Printer', None)
        return Device(manufacturer, 'printer', 'Printer', None)
    elif match := re.search(r'HP(?: Color)? LaserJet MFP (\w+)', r.text):
        return Device('HP', f'LaserJet {match.group(1)}', 'MFP', None)
    elif match := re.search(r'HP(?: Color)? LaserJet Pro MFP (\w+)', r.text):
        return Device('HP', f'LaserJet Pro {match.group(1)}', 'MFP', None)
    elif 'FreeNAS' in r.text:
        return Device('Generic', 'FreeNAS', 'NAS', 'FreeBSD')
    elif str_contains(r.text, ('Avaya', 'IP Office')):
        if 'IP Office Application Server' in r.text:
            return Device('Avaya', 'IP Office application server', 'Server', None)
        return Device('Avaya', 'IP Office', 'IP PBX', None)

    # Try to detect and follow HTML redirect
    if ('Content-Type' in r.headers) and (r.headers['Content-Type'] == 'text/html'):
        url = html.fromstring(r.text).xpath('//meta[@http-equiv="refresh"]/@content')
        if url:
            url = url[0][url[0].lower().find('url=') + 4:]
            r = safe_http_get(f'{base_url}/{url}')
            if str_contains(r.text, ('Bizerba GmbH & Co. KG', 'Labeler Master', 'homepage.html')):
                # Bizerba labeling system
                return Device('Bizerba', 'Labeler master', 'Labeling system', None)
            elif ('Naim Configuration' in r.text) or ('Mu-so Configuration' in r.text):
                # Naim network audio device
                return Device('Naim', 'network media device', 'Other', 'Linux')
            elif 'NAS01' in r.text:
                return Device('Generic', 'NAS', 'NAS', None)


def snmp_get(ip: str, oid: Union[str, tuple], port=161, snmp_community: str = 'public') -> Optional[str]:
    """
    Send SNMP Get requests
    :param ip: target host IP address
    :param oid: SNMP object identifier
    :param port: target host UDP port
    :param snmp_community: SNMP community string to authorize
    """
    if isinstance(oid, tuple):
        object_identity = ObjectIdentity(*oid)
    else:
        object_identity = ObjectIdentity(oid)
    iterator = getCmd(SnmpEngine(), CommunityData(snmp_community), UdpTransportTarget((ip, port)),
                      ContextData(), ObjectType(object_identity))
    error_indication, error_status, error_index, var_binds = next(iterator)
    if error_indication or error_status:
        return None
    return var_binds[0].prettyPrint().split('=')[-1].strip()


def recognize_by_snmp(ip: str, port=161, snmp_community: str = 'public') -> Optional[Device]:
    """Recognize device with a built-in SNMP agent, based on standard SNMP MIBs"""
    if system_description := snmp_get(ip, ('SNMPv2-MIB', 'sysDescr', 0), port, snmp_community):
        if system_description.startswith('UAP-AC-LR'):
            return Device('Ubiquiti', 'UniFi AP AC LR', 'Wi-Fi AP', 'AirOS')
        elif system_description.startswith('UAP-AC-Mesh'):
            return Device('Ubiquiti', 'UniFi AC Mesh', 'Wi-Fi Mesh', 'AirOS')
