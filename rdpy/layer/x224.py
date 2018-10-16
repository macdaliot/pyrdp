from rdpy.core.newlayer import Layer
from rdpy.core.subject import ObservedBy
from rdpy.enum.x224 import X224Header
from rdpy.parser.x224 import X224Parser
from rdpy.pdu.x224 import X224DataPDU, X224ConnectionRequestPDU, X224ConnectionConfirmPDU, X224DisconnectRequestPDU, X224ErrorPDU
from rdpy.protocol.x224.layer import X224Observer


@ObservedBy(X224Observer)
class X224Layer(Layer):
    """
    Layer to handle X224-related traffic
    ObservedBy: X224Observer
    """

    def __init__(self):
        Layer.__init__(self)
        self.parser = X224Parser()
        self.handlers = {}

    def recv(self, data):
        """
        Receive a X.224 message, decode its header, notify the observer and forward to the next layer
        if its a data PDU.
        :param data: The X.224 raw data (with header and payload)
        :type data: str
        """
        pdu = self.parser.parse(data)
        self.pduReceived(pdu, pdu.header == X224Header.X224_TPDU_DATA)

    def send(self, payload, roa=False, eot=True):
        """
        Encapsulate the payload in a X.224 Data PDU and send it to the upper layer.
        :type payload: str
        :param eot: End of transmission.
        :param roa: Request of acknowledgement
        """

        pdu = X224DataPDU(roa, eot, payload)
        self.previous.send(self.parser.write(pdu))

    def sendConnectionPDU(self, factory, payload, **kwargs):
        """
        :param factory: The PDU class to use to create the connection PDU
        :type factory: Class
        :type payload: str
        """
        credit = kwargs.pop("credit", 0)
        destination = kwargs.pop("destination", 0)
        source = kwargs.pop("source", 0)
        options = kwargs.pop("options", 0)

        pdu = factory(credit, destination, source, options, payload)
        self.previous.send(self.parser.write(pdu))

    def sendConnectionRequest(self, payload, **kwargs):
        self.sendConnectionPDU(X224ConnectionRequestPDU, payload, **kwargs)

    def sendConnectionConfirm(self, payload, **kwargs):
        self.sendConnectionPDU(X224ConnectionConfirmPDU, payload, **kwargs)

    def sendDisconnectRequest(self, reason, **kwargs):
        destination = kwargs.pop("destination", 0)
        source = kwargs.pop("source", 0)
        payload = kwargs.pop("payload", "")

        pdu = X224DisconnectRequestPDU(destination, source, reason, payload)
        self.previous.send(self.parser.write(pdu))

    def sendError(self, cause, **kwargs):
        destination = kwargs.pop("destination", 0)

        pdu = X224ErrorPDU(destination, cause)
        self.previous.send(self.parser.write(pdu))