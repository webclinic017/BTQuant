from live_strategys.live_functions import *
import backtrader as bt

trade_logger = setup_logger('TradeLogger', 'QQE_Example_Trade_Monitor.log', level=logging.DEBUG)

class VolumeOscillator(bt.Indicator):
    lines = ('short', 'long', 'osc')
    params = (('shortlen', 5),
            ('longlen', 10))
    
    def __init__(self):
        shortlen, longlen = self.params.shortlen, self.params.longlen
        self.lines.short = bt.indicators.ExponentialMovingAverage(self.data.volume, period=shortlen)
        self.lines.long = bt.indicators.ExponentialMovingAverage(self.data.volume, period=longlen)

    def next(self):
        if self.lines.long[0] > 0:
            self.lines.osc[0] = (self.lines.short[0] - self.lines.long[0]) / self.lines.long[0] * 100
        else:
            self.lines.osc[0] = 0

class QQEIndicator(bt.Indicator):
    params = (
        ("period", 6),
        ("fast", 5),
        ("q", 3.0),
        ("debug", False)
    )
    lines = ("qqe_line",)

    def __init__(self):
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.period)
        self.atr = bt.indicators.ATR(self.data, period=self.p.fast)
        self.dar = bt.If(self.atr > 0, bt.indicators.EMA(self.atr - self.p.q, period=int((self.p.period * 2) - 1)), 0)
        self.lines.qqe_line = bt.If(self.rsi > 0, self.rsi + self.dar, 0)

    def next(self):
        # check if ATR is not zero to avoid division by zero errors
        if self.atr[0] == 0:
            print("ATR is zero, skipping this iteration to avoid division by zero.")
            return

        # check if RSI and DAR are valid before computing the QQE line
        if self.rsi[0] != 0 and self.dar[0] != 0:
            self.lines.qqe_line[0] = self.rsi[0] + self.dar[0]
        else:
            self.lines.qqe_line[0] = 0
        
        if self.p.debug:
            print(f"RSI: {self.rsi[0]}, DAR: {self.dar[0]}, ATR: {self.atr[0]}, QQE: {self.lines.qqe_line[0]}")

class QQE_Example(BaseStrategy):
    params = (
        ("ema_length", 20),
        ('hull_length', 53),
        ("printlog", True),
        ('percent_sizer', 0.01), # 0.01 -> 1%
        ('take_profit', 1),
        ("backtest", None)
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.qqe = QQEIndicator(self.data)
        self.hma = bt.indicators.HullMovingAverage(self.data, period=self.p.hull_length)
        self.ema = bt.indicators.EMA(self.data.close, period=self.params.ema_length)
        self.volosc = VolumeOscillator(self.data)
        self.DCA = False
        self.buy_executed = False
        self.conditions_checked = False
        
        # Forensic Logging
        self.trade_cycles = 0
        self.total_profit_usd = 0
        self.last_profit_usd = 0
        self.start_time = datetime.utcnow()
        self.position_start_time = None
        self.max_buys_per_cycle = 0
        self.total_buys = 0
        self.current_cycle_buys = 0

    def buy_or_short_condition(self):
        print('buy_or_short_condition')
        if not self.buy_executed and not self.conditions_checked:
            if (self.qqe.qqe_line[-1] > 0) and \
                (self.data.close[-1] > self.hma[0]) and \
                (self.volosc.osc[-1] > self.volosc.lines[0]
            ):
                if self.params.backtest == False:
                    self.entry_prices.append(self.data.close[0])
                    self.sizes.append(self.amount)
                    self.enqueue_order('buy', exchange=self.exchange, account=self.account, asset=self.asset, amount=self.amount)
                    self.calc_averages()
                    self.buy_executed = True
                    self.conditions_checked = True
                    self.log_entry()
                elif self.params.backtest == True:
                    self.buy(size=self.stake, price=self.data.close[0], exectype=bt.Order.Market)
                    self.buy_executed = True
                    self.entry_prices.append(self.data.close[0])
                    self.sizes.append(self.stake)
                    self.calc_averages()
                    self.log_entry()

    def sell_or_cover_condition(self):
        print('sell_or_cover_condition')
        if self.buy_executed and (self.qqe.qqe_line[-1] > 0) and \
            (self.data.close[-1] < self.hma[0]) and \
            (self.volosc.osc[-1] < self.volosc.lines[0]
        ):
            if self.params.backtest == False:
                self.enqueue_order('sell', exchange=self.exchange, account=self.account, asset=self.asset)
            elif self.params.backtest == True:
                self.close()
                
            self.log_exit("Sell Signal - Take Profit")
            self.reset_position_state()
            self.buy_executed = False
            self.conditions_checked = True

    def next(self):
        BaseStrategy.next(self)
        self.conditions_checked = False

    def log_entry(self):
        trade_logger.debug("-" * 100)
        self.total_buys += 1
        self.current_cycle_buys += 1
        self.max_buys_per_cycle = max(self.max_buys_per_cycle, self.current_cycle_buys)

        trade_logger.debug(f"{datetime.utcnow()} - Buy executed: {self.data._name}")
        trade_logger.debug(f"Entry price: {self.entry_prices[-1]:.12f}")
        trade_logger.debug(f"Position size: {self.sizes[-1]}")
        trade_logger.debug(f"Current cash: {self.broker.getcash():.2f}")
        trade_logger.debug(f"Current portfolio value: {self.broker.getvalue():.2f}")
        trade_logger.debug("*" * 100)

    def log_exit(self, exit_type):
        trade_logger.info("-" * 100)
        trade_logger.info(f"{datetime.utcnow()} - {exit_type} executed: {self.data._name}")
        
        position_size = sum(self.sizes)
        exit_price = self.data.close[0]
        profit_usd = (exit_price - self.average_entry_price) * position_size
        self.last_profit_usd = profit_usd
        self.total_profit_usd += profit_usd
        self.trade_cycles += 1
        
        trade_logger.info(f"Exit price: {exit_price:.12f}")
        trade_logger.info(f"Average entry price: {self.average_entry_price:.12f}")
        trade_logger.info(f"Position size: {position_size}")
        trade_logger.info(f"Profit for this cycle (USD): {profit_usd:.2f}")
        trade_logger.info(f"Total profit (USD): {self.total_profit_usd:.2f}")
        trade_logger.info(f"Trade cycles completed: {self.trade_cycles}")
        trade_logger.info(f"Average profit per cycle (USD): {self.total_profit_usd / self.trade_cycles:.2f}")
        trade_logger.info(f"Time elapsed: {datetime.utcnow() - self.start_time}")
        if self.position_start_time:
            trade_logger.info(f"Position cycle time: {datetime.utcnow() - self.position_start_time}")
        trade_logger.info(f"Maximum buys per cycle: {self.max_buys_per_cycle}")
        trade_logger.info(f"Total buys: {self.total_buys}")
        trade_logger.info("*" * 100)
        
        self.current_cycle_buys = 0
        self.position_start_time = None

    def stop(self):
        self.order_queue.put(None)
        self.order_thread.join()
        print('Final Portfolio Value: %.2f' % self.broker.getvalue())