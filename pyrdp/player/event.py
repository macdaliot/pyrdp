from typing import Optional

from PyQt4 import QtGui
from PyQt4.QtGui import QTextCursor

from pyrdp.core import decodeUTF16LE
from pyrdp.core.scancode import scancodeToChar
from pyrdp.enum import BitmapFlags, CapabilityType, FastPathFragmentation, KeyboardFlag, ParserMode, SlowPathUpdateType
from pyrdp.layer import PlayerMessageObserver
from pyrdp.logging import log
from pyrdp.parser import BasicFastPathParser, BitmapParser, ClientInfoParser, ClipboardParser, FastPathOutputParser, \
    SlowPathParser
from pyrdp.parser.rdp.connection import ClientConnectionParser
from pyrdp.pdu import BitmapUpdateData, ConfirmActivePDU, FastPathBitmapEvent, FastPathMouseEvent, FastPathOrdersEvent, \
    FastPathScanCodeEvent, FormatDataResponsePDU, InputPDU, KeyboardEvent, MouseEvent, PlayerMessagePDU, UpdatePDU
from pyrdp.pdu.rdp.fastpath import FastPathOutputUpdateEvent
from pyrdp.ui import RDPBitmapToQtImage


class PlayerMessageHandler(PlayerMessageObserver):
    """
    Class to manage the display of the RDP player when reading events.
    """

    def __init__(self, viewer, text):
        PlayerMessageObserver.__init__(self)
        self.viewer = viewer
        self.text = text
        self.writeInCaps = False

        self.inputParser = BasicFastPathParser(ParserMode.SERVER)
        self.outputParser = BasicFastPathParser(ParserMode.CLIENT)
        self.clientInfoParser = ClientInfoParser()
        self.dataParser = SlowPathParser()
        self.clipboardParser = ClipboardParser()
        self.outputEventParser = FastPathOutputParser()
        self.clientConnectionParser = ClientConnectionParser()

        self.buffer = b""

    def onConnectionClose(self, pdu: PlayerMessagePDU):
        self.text.moveCursor(QTextCursor.End)
        self.text.insertPlainText("\n<Connection closed>")

    def onOutput(self, pdu: PlayerMessagePDU):
        pdu = self.outputParser.parse(pdu.payload)

        for event in pdu.events:
            reassembledEvent = self.reassembleEvent(event)
            if reassembledEvent is not None:
                if isinstance(reassembledEvent, FastPathOrdersEvent):
                    log.debug("Not handling orders event, not coded :)")
                elif isinstance(reassembledEvent, FastPathBitmapEvent):
                    log.debug("Handling bitmap event %(arg1)s", {"arg1": type(reassembledEvent)})
                    self.onBitmap(reassembledEvent)
                else:
                    log.debug("Can't handle output event: %(arg1)s", {"arg1": type(reassembledEvent)})
            else:
                log.debug("Reassembling output event...")

    def onInput(self, pdu: PlayerMessagePDU):
        pdu = self.inputParser.parse(pdu.payload)

        for event in pdu.events:
            if isinstance(event, FastPathScanCodeEvent):
                log.debug("handling %(arg1)s", {"arg1": event})
                self.onScanCode(event.scancode, not event.isReleased)
            elif isinstance(event, FastPathMouseEvent):
                self.onMousePosition(event.mouseX, event.mouseY)
            else:
                log.debug("Can't handle input event: %(arg1)s", {"arg1": event})

    def onScanCode(self, code: int, isPressed: bool):
        """
        Handle scan code.
        """
        log.debug("Reading scancode %(arg1)s", {"arg1": code})

        if code in [0x2A, 0x36]:
            self.text.moveCursor(QTextCursor.End)
            self.text.insertPlainText("\n<LSHIFT PRESSED>" if isPressed else "\n<LSHIFT RELEASED>")
            self.writeInCaps = not self.writeInCaps
        elif code == 0x3A and isPressed:
            self.text.moveCursor(QTextCursor.End)
            self.text.insertPlainText("\n<CAPSLOCK>")
            self.writeInCaps = not self.writeInCaps
        elif isPressed:
            char = scancodeToChar(code)
            self.text.moveCursor(QtGui.QTextCursor.End)
            self.text.insertPlainText(char if self.writeInCaps else char.lower())

    def onMousePosition(self, x: int, y: int):
        self.viewer.setMousePosition(x, y)

    def onBitmap(self, event: FastPathBitmapEvent):
        parsedEvent = self.outputEventParser.parseBitmapEvent(event)
        for bitmapData in parsedEvent.bitmapUpdateData:
            self.handleBitmap(bitmapData)

    def handleBitmap(self, bitmapData: BitmapUpdateData):
        image = RDPBitmapToQtImage(bitmapData.width, bitmapData.heigth, bitmapData.bitsPerPixel, bitmapData.flags & BitmapFlags.BITMAP_COMPRESSION != 0, bitmapData.bitmapData)
        self.viewer.notifyImage(bitmapData.destLeft, bitmapData.destTop, image,
                                bitmapData.destRight - bitmapData.destLeft + 1,
                                bitmapData.destBottom - bitmapData.destTop + 1)

    def onClientInfo(self, pdu: PlayerMessagePDU):
        clientInfoPDU = self.clientInfoParser.parse(pdu.payload)
        self.text.insertPlainText("USERNAME: {}\nPASSWORD: {}\nDOMAIN: {}\n"
                                  .format(clientInfoPDU.username.replace("\0", ""),
                                          clientInfoPDU.password.replace("\0", ""),
                                          clientInfoPDU.domain.replace("\0", "")))
        self.text.insertPlainText("--------------------\n")

    def onSlowPathPDU(self, pdu: PlayerMessagePDU):
        pdu = self.dataParser.parse(pdu.payload)

        if isinstance(pdu, ConfirmActivePDU):
            self.viewer.resize(pdu.parsedCapabilitySets[CapabilityType.CAPSTYPE_BITMAP].desktopWidth,
                               pdu.parsedCapabilitySets[CapabilityType.CAPSTYPE_BITMAP].desktopHeight)
        elif isinstance(pdu, UpdatePDU) and pdu.updateType == SlowPathUpdateType.SLOWPATH_UPDATETYPE_BITMAP:
            updates = BitmapParser().parseBitmapUpdateData(pdu.updateData)
            for bitmap in updates:
                self.handleBitmap(bitmap)
        elif isinstance(pdu, InputPDU):
            for event in pdu.events:
                if isinstance(event, MouseEvent):
                    self.onMousePosition(event.x, event.y)
                elif isinstance(event, KeyboardEvent):
                    self.onScanCode(event.keyCode, event.flags & KeyboardFlag.KBDFLAGS_DOWN != 0)

    def onClipboardData(self, pdu: PlayerMessagePDU):
        formatDataResponsePDU: FormatDataResponsePDU = self.clipboardParser.parse(pdu.payload)
        self.text.moveCursor(QtGui.QTextCursor.End)
        self.text.insertPlainText("\n=============\n")
        self.text.insertPlainText("CLIPBOARD DATA: {}".format(decodeUTF16LE(formatDataResponsePDU.requestedFormatData)))
        self.text.insertPlainText("\n=============\n")

    def onClientData(self, pdu: PlayerMessagePDU):
        """
        Prints the clientName on the screen
        """
        clientDataPDU = self.clientConnectionParser.parse(pdu.payload)
        self.text.moveCursor(QtGui.QTextCursor.End)
        self.text.insertPlainText("--------------------\n")
        self.text.insertPlainText(f"HOST: {clientDataPDU.coreData.clientName.strip(chr(0))}\n")

    def reassembleEvent(self, event: FastPathOutputUpdateEvent) -> Optional[FastPathBitmapEvent]:
        """
        Handles FastPath event reassembly as described in
        https://msdn.microsoft.com/en-us/library/cc240622.aspx
        :param event: A potentially segmented fastpath output event
        """
        fragmentationFlag = FastPathFragmentation((event.header & 0b00110000) >> 4)
        if fragmentationFlag == FastPathFragmentation.FASTPATH_FRAGMENT_SINGLE:
            return event
        elif fragmentationFlag == FastPathFragmentation.FASTPATH_FRAGMENT_FIRST:
            self.buffer = event.payload
        elif fragmentationFlag == FastPathFragmentation.FASTPATH_FRAGMENT_NEXT:
            self.buffer += event.payload
        elif fragmentationFlag == FastPathFragmentation.FASTPATH_FRAGMENT_LAST:
            self.buffer += event.payload
            event.payload = self.buffer
            return self.outputEventParser.parseBitmapEvent(event)

        return None
