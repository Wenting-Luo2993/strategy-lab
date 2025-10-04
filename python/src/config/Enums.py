from enum import Enum

class Regime(Enum):
    """
    Enum representing different ticker regimes based on indicator values.
    """
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"