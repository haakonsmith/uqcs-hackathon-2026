import time
from dataclasses import dataclass


@dataclass
class currentAction:
    mouseHover: bool = False
    mousePos: tuple = (0, 0)
    mouseTile: tuple = (0, 0)
    mouseClick: bool = False  # temp i guess


class player:
    def __init__(self, websocket):
        self._websocket = websocket
        self._userID = str(websocket.id)
        self._name = None
        self._status = None
        self._territory = None
        self._points = 0
        self._connectionTime = time.time()
        self._lastSeen = self._connectionTime
        self._currentAction = currentAction()

    def getName(self):
        return self._name

    def getWebSocket(self):
        return self._websocket

    def setName(self, name):
        self._name = name

    def setStatus(self, status):
        self._status = status

    def setTerritory(self, territory):
        self._territory = territory

    def setPoints(self, points):
        self._points = points

    def setAction(self, currentAction):
        self._currentAction = currentAction

    def getStatus(self):
        return self._status
