"""Deterministic offline options exchange. Never communicates with a broker."""
from dataclasses import dataclass
from datetime import date
from lavish_core.utils.safety import finite_number

@dataclass(frozen=True)
class Contract:
    symbol: str
    underlying: str
    expiry: date
    side: str
    strike: float
    multiplier: int = 100

class OptionsExchange:
    synthetic = True

    def __init__(self, cash=100000, fee=.65, today=None):
        self.cash=finite_number(cash,nonnegative=True)
        self.fee=finite_number(fee,nonnegative=True)
        self.today=today or date.today()
        self.contracts={}; self.quotes={}; self.orders={}; self.clients={}
        self.positions={}; self.shares={}; self.events=[]

    def add_contract(self, contract, bid, ask, liquidity=1000):
        if contract.multiplier!=100 or contract.side not in ('CALL','PUT'):
            raise ValueError('Only standard long calls and puts are supported')
        finite_number(contract.strike,positive=True)
        self.contracts[contract.symbol]=contract
        self.quote(contract.symbol,bid,ask,liquidity)

    def quote(self,symbol,bid,ask,liquidity=1000):
        bid=finite_number(bid,nonnegative=True); ask=finite_number(ask,positive=True)
        if bid>ask or isinstance(liquidity,bool) or int(liquidity)!=liquidity or liquidity<0:
            raise ValueError('Invalid market')
        self.quotes[symbol]=[bid,ask,int(liquidity)]

    def submit(self,symbol,side,qty,limit_price,client_id):
        if client_id in self.clients:return self.orders[self.clients[client_id]]
        qty=finite_number(qty,positive=True); limit_price=finite_number(limit_price,positive=True)
        if int(qty)!=qty or side not in ('BUY','SELL') or not client_id:raise ValueError('Invalid order')
        contract=self.contracts[symbol]
        if contract.expiry<=self.today:raise ValueError('Expired or expiry-day contract')
        pending=[o for o in self.orders.values() if o['status'] in ('new','partially_filled','pending_cancel')]
        if side=='BUY':
            reserved=sum((o['qty']-o['filled_qty'])*(o['limit_price']*100+self.fee) for o in pending if o['side']=='BUY')
            if qty*(limit_price*100+self.fee)>self.cash-reserved:raise ValueError('Insufficient cash')
        else:
            reserved=sum(o['qty']-o['filled_qty'] for o in pending if o['side']=='SELL' and o['symbol']==symbol)
            if qty>self.positions.get(symbol,0)-reserved:raise ValueError('Cannot open a short position')
        oid='sim-'+str(len(self.orders)+1)
        order=dict(id=oid,symbol=symbol,side=side,qty=int(qty),limit_price=limit_price,client_order_id=client_id,status='new',filled_qty=0,filled_avg_price=None,filled_fee=0.)
        self.orders[oid]=order;self.clients[client_id]=oid
        self.match(oid)
        return order

    def match(self,oid):
        order=self.orders[oid]
        if order['status'] not in ('new','partially_filled','pending_cancel'):return order
        market=self.quotes[order['symbol']]; price=market[1] if order['side']=='BUY' else market[0]
        if price<=0 or (order['side']=='BUY' and price>order['limit_price']) or (order['side']=='SELL' and price<order['limit_price']):return order
        qty=min(order['qty']-order['filled_qty'],market[2])
        if not qty:return order
        prior=order['filled_qty']; direction=1 if order['side']=='BUY' else -1
        self.cash-=direction*qty*price*100+qty*self.fee
        self.positions[order['symbol']]=self.positions.get(order['symbol'],0)+direction*qty
        if not self.positions[order['symbol']]:del self.positions[order['symbol']]
        order['filled_avg_price']=((order['filled_avg_price'] or 0)*prior+price*qty)/(prior+qty)
        order['filled_qty']+=qty;order['filled_fee']+=qty*self.fee
        order['status']='filled' if order['filled_qty']==order['qty'] else 'partially_filled'
        market[2]-=qty
        self.events.append(dict(symbol=order['symbol'],side=order['side'],qty=qty,price=price,fee=qty*self.fee))
        return order

    def cancel(self,oid,confirmed=True):
        order=self.orders[oid]
        if order['status']=='filled':return False
        order['status']='canceled' if confirmed else 'pending_cancel'
        return True

    def settle(self,symbol,underlying_price):
        contract=self.contracts[symbol]
        if self.today<=contract.expiry:raise ValueError('Not expired')
        price=finite_number(underlying_price,positive=True)
        intrinsic=max(0,(price-contract.strike) if contract.side=='CALL' else (contract.strike-price))
        if intrinsic:return dict(status='exercise_review_required',synthetic=True)
        self.positions.pop(symbol,None)
        return dict(status='expired_worthless',synthetic=True)

    def exercise(self,symbol):
        contract=self.contracts[symbol]; qty=self.positions.get(symbol,0); shares=qty*100
        if self.today<=contract.expiry or qty<=0:raise ValueError('No expired long holding')
        if contract.side=='CALL':
            if self.cash<shares*contract.strike:raise ValueError('Insufficient exercise cash')
            self.cash-=shares*contract.strike;self.shares[contract.underlying]=self.shares.get(contract.underlying,0)+shares
        else:
            if self.shares.get(contract.underlying,0)<shares:raise ValueError('Delivery review required')
            self.shares[contract.underlying]-=shares;self.cash+=shares*contract.strike
        del self.positions[symbol]
        return dict(synthetic=True,status='exercised',shares=shares,cash=self.cash)
