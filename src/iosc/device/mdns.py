import select
import socket
import struct
import time
from dataclasses import dataclass, field

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353

TYPE_A = 1
TYPE_PTR = 12
TYPE_SRV = 33
CLASS_IN = 1

HEADER = struct.Struct(">HHHHHH")
FLAG_RESPONSE = 0x8000

# iphones with wifi connections enabled advertise this
MOBILE_DEVICE_SERVICE = "_apple-mobdev2._tcp.local"


@dataclass
class ServiceInstance:
    name: str
    host: str | None = None
    port: int | None = None
    addresses: list[str] = field(default_factory=list)


@dataclass
class Answers:
    pointers: dict[str, set[str]] = field(default_factory=dict)
    services: dict[str, tuple[str, int]] = field(default_factory=dict)
    addresses: dict[str, set[str]] = field(default_factory=dict)

    def merge(self, other: "Answers") -> None:
        for key, values in other.pointers.items():
            self.pointers.setdefault(key, set()).update(values)
        self.services.update(other.services)
        for key, values in other.addresses.items():
            self.addresses.setdefault(key, set()).update(values)


def encode_name(name: str) -> bytes:
    out = bytearray()
    for label in name.rstrip(".").split("."):
        raw = label.encode("utf-8")
        out.append(len(raw))
        out += raw
    out.append(0)
    return bytes(out)


def build_query(name: str, record_type: int) -> bytes:
    # id 0, standard query, one question
    return HEADER.pack(0, 0, 1, 0, 0, 0) + encode_name(name) + struct.pack(
        ">HH", record_type, CLASS_IN
    )


def decode_name(packet: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    end: int | None = None
    jumps = 0
    while True:
        if offset >= len(packet):
            raise ValueError("name runs past the packet")
        length = packet[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError("truncated name pointer")
            if end is None:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | packet[offset + 1]
            jumps += 1
            if jumps > 32:
                raise ValueError("name pointer loop")
            continue
        offset += 1
        if length == 0:
            break
        labels.append(packet[offset : offset + length].decode("utf-8", "replace"))
        offset += length
    return ".".join(labels), end if end is not None else offset


def parse_response(packet: bytes) -> Answers:
    answers = Answers()
    if len(packet) < HEADER.size:
        return answers
    _id, flags, qdcount, ancount, nscount, arcount = HEADER.unpack_from(packet)
    if not flags & FLAG_RESPONSE:
        return answers

    offset = HEADER.size
    for _ in range(qdcount):
        _name, offset = decode_name(packet, offset)
        offset += 4

    for _ in range(ancount + nscount + arcount):
        name, offset = decode_name(packet, offset)
        record_type, _class, _ttl, length = struct.unpack_from(">HHIH", packet, offset)
        offset += 10
        data_offset = offset
        offset += length
        key = name.lower()
        if record_type == TYPE_PTR:
            target, _ = decode_name(packet, data_offset)
            answers.pointers.setdefault(key, set()).add(target)
        elif record_type == TYPE_SRV:
            _priority, _weight, port = struct.unpack_from(">HHH", packet, data_offset)
            target, _ = decode_name(packet, data_offset + 6)
            answers.services[key] = (target, port)
        elif record_type == TYPE_A and length == 4:
            address = socket.inet_ntoa(packet[data_offset : data_offset + 4])
            answers.addresses.setdefault(key, set()).add(address)
    return answers


def local_ipv4_addresses() -> list[str]:
    found: list[str] = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return found
    for info in infos:
        address = info[4][0]
        if not address.startswith("127.") and address not in found:
            found.append(address)
    return found


def open_sockets(interface: str | None) -> list[socket.socket]:
    sources: list[str | None] = [interface] if interface else local_ipv4_addresses()
    if not sources:
        sources = [None]
    opened: list[socket.socket] = []
    for source in sources:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            if source:
                sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(source)
                )
            # ephemeral source port makes responders answer by unicast
            # TODO the iphone on home wifi never answers this unicast query. Query from 5353 and read the multicast answer
            sock.bind((source or "", 0))
        except OSError:
            sock.close()
            continue
        sock.setblocking(False)
        opened.append(sock)
    return opened


def exchange(
    queries: list[bytes], timeout: float, interface: str | None = None
) -> Answers:
    answers = Answers()
    sockets = open_sockets(interface)
    try:
        for sock in sockets:
            for query in queries:
                try:
                    sock.sendto(query, (MDNS_GROUP, MDNS_PORT))
                except OSError:
                    pass
        deadline = time.monotonic() + timeout
        while sockets:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            readable, _, _ = select.select(sockets, [], [], remaining)
            for sock in readable:
                try:
                    packet, _ = sock.recvfrom(9000)
                except OSError:
                    continue
                try:
                    answers.merge(parse_response(packet))
                except (ValueError, struct.error):
                    continue
    finally:
        for sock in sockets:
            sock.close()
    return answers


def browse(
    service: str = MOBILE_DEVICE_SERVICE,
    timeout: float = 2.0,
    interface: str | None = None,
) -> list[ServiceInstance]:
    answers = exchange([build_query(service, TYPE_PTR)], timeout, interface)
    names = sorted(answers.pointers.get(service.lower(), set()))

    # responders may leave srv or address records out of the first answer
    follow_up = [build_query(n, TYPE_SRV) for n in names if n.lower() not in answers.services]
    if follow_up:
        answers.merge(exchange(follow_up, timeout / 2, interface))
    hosts = {target for target, _ in answers.services.values()}
    missing = [build_query(h, TYPE_A) for h in hosts if h.lower() not in answers.addresses]
    if missing:
        answers.merge(exchange(missing, timeout / 2, interface))

    instances: list[ServiceInstance] = []
    for name in names:
        instance = ServiceInstance(name)
        srv = answers.services.get(name.lower())
        if srv is not None:
            instance.host, instance.port = srv
            instance.addresses = sorted(answers.addresses.get(srv[0].lower(), set()))
        instances.append(instance)
    return instances
