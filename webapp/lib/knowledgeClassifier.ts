// Query classification for the Knowledge Base chat.
// Determines whether a user query is F5-product-specific, RFC/protocol-specific,
// or general — used to select which document sources to search.

export type QueryMode = 'f5' | 'rfc' | 'general';

// F5 product/configuration terms — things you configure on a BIG-IP or F5OS device
const F5_TERMS: string[] = [
    'irule', 'i-rule', 'bigip', 'big-ip', 'ltm', 'gtm', 'apm', 'asm', 'afm',
    'virtual server', 'vip', 'pool member', 'pool ', 'health monitor', 'node ',
    'ssl profile', 'client ssl', 'server ssl', 'tcp profile', 'http profile',
    'oneconnect', 'persistence', 'cookie persist', 'source persist', 'ssl offload',
    'snat', ' nat ', 'partition', 'route domain', 'traffic group',
    'sync group', 'device group', 'failover', 'tmsh', 'tmos', 'icontrol',
    'f5os', 'velos', 'rseries', 'r-series', 'bigiq', 'big-iq',
    'iapp', 'traffic policy', 'local traffic policy', 'data group',
    'self ip', 'vlan ', 'trunk ', 'management port', 'wide ip',
    'compression profile', 'websafe', 'pem ', 'cgnat', 'dos profile',
    'waf policy', 'asm policy', 'security policy', 'monitor ', 'profile ',
    ' f5 ', 'f5 big',
    // F5 Support KB / Security Advisory signals
    'cve-', 'security advisory', 'k-article', 'bug id', 'id number',
    'sol ', 'sol:', 'f5 support', 'my.f5.com', 'distributed cloud', 'f5 xc',
    'xc tenant', 'xc namespace', 'waap', 'bot defense', 'api security',
    'enterprise manager', 'big-iq', 'iworkflow',
];

// RFC / protocol-mechanics terms — things described in standards documents.
// Includes HTTP semantics (status codes, methods, headers) since those are
// defined in RFCs (7230/7231/9110) not vendor documentation.
const RFC_TERMS: string[] = [
    // Explicit RFC references
    'rfc ', 'rfc-', 'rfc2', 'rfc3', 'rfc4', 'rfc5', 'rfc6', 'rfc7', 'rfc8', 'rfc9',
    'ietf', 'draft-ietf',

    // HTTP semantics — status codes, methods, headers (RFC 7230/7231/9110)
    'status code', 'http status', 'response code', 'http code', 'http codes',
    'response codes', 'status codes',
    '1xx', '2xx', '3xx', '4xx', '5xx',
    '100 ', '101 ', '200 ', '201 ', '204 ',
    '300 ', '301 ', '302 ', '303 ', '304 ', '307 ', '308 ',
    '400 ', '401 ', '403 ', '404 ', '405 ', '409 ', '429 ',
    '500 ', '502 ', '503 ', '504 ',
    'moved permanently', 'temporary redirect', 'not modified', 'bad request',
    'unauthorized', 'forbidden', 'not found', 'bad gateway', 'service unavailable',
    'http method', 'get request', 'post request', 'put request', 'delete request',
    'http header', 'request header', 'response header', 'content-type', 'cache-control',
    'http redirect', 'location header', 'http version',

    // TLS/SSL protocol mechanics
    'client hello', 'server hello', 'handshake', 'cipher suite', 'certificate exchange',
    'key exchange', 'diffie-hellman', 'elliptic curve', 'hmac', 'mac algorithm',
    'record layer', 'alert protocol', 'change cipher spec', 'master secret',
    'pre-master secret', 'certificate verify', 'finished message',
    'tls 1.', 'tls1.', 'protocol negotiation',

    // TCP/IP mechanics
    '3-way handshake', 'three-way handshake', 'syn ', 'syn/ack', 'fin/ack',
    'tcp segment', 'udp datagram', 'connection setup', 'connection teardown',
    'session establishment', 'window size', 'congestion control', 'slow start',
    'ip header', 'tcp header', 'tcp/ip stack',

    // Network protocols
    'packet ', 'layer 2', 'layer 3', 'layer 4',
    'osi model', 'osi layer', 'encapsulation', 'fragmentation', 'mtu ', 'pmtu',
    'bgp ', 'ospf ', 'eigrp ', 'rip ', 'isis ', 'mpls ', 'evpn',
    'icmp ', 'arp ', 'dhcp protocol', 'dns protocol', 'radius protocol',
    'dscp', 'diffserv', 'ecn ',

    // HTTP versions
    'http/1', 'http/2', 'http/3', 'quic',

    // Misc protocol terms
    'socket', 'port number', 'well-known port', 'ephemeral port',
];

// K-number pattern — K followed by 4+ digits (K14190, K000133373, etc.)
const K_NUMBER_RE = /\bk\d{4,}\b/i;

// CVE pattern — CVE-YYYY-NNNNN
const CVE_RE = /\bcve-\d{4}-\d+\b/i;

// iRules Tcl command namespace separator (e.g. TCP::collect, HTTP::redirect, SSL::cipher)
const IRULE_CMD_RE = /\b\w+::\w+/i;

export function classifyQuery(query: string): QueryMode {
    const q = query.toLowerCase();
    let f5Score = 0;
    let rfcScore = 0;

    // Strong F5 signals: K-articles, CVEs, and iRules Tcl commands (NAMESPACE::command)
    if (K_NUMBER_RE.test(q))   f5Score += 3;
    if (CVE_RE.test(q))        f5Score += 3;
    if (IRULE_CMD_RE.test(q))  f5Score += 3;

    for (const term of F5_TERMS) {
        if (q.includes(term)) f5Score++;
    }
    for (const term of RFC_TERMS) {
        if (q.includes(term)) rfcScore++;
    }

    if (f5Score > rfcScore) return 'f5';
    if (rfcScore > f5Score) return 'rfc';
    return 'general';
}
