from ib_insync import *

ib = IB()
ib.connect("127.0.0.1", 4002, clientId=99)

contract = Stock("QQQ", "SMART", "USD")
ib.qualifyContracts(contract)

ib.reqMarketDataType(1)

ticker = ib.reqMktData(contract)

ib.sleep(5)

print("Market Data Type:", ticker.marketDataType)
print("Bid:", ticker.bid)
print("Ask:", ticker.ask)
print("Last:", ticker.last)

ib.disconnect()