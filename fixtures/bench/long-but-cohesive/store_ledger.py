"""Store till ledger (fixture: long, single responsibility, cohesive by LCOM4).

Every money movement books through _book onto the same entry log, so
the LCOM4 method graph stays one connected group: the file is long by
line count and one responsibility by structure - exactly the context a
size-only budget cannot see, and the case the context-aware verdict
exists to name.
"""


class StoreLedger:
    """One till's money movements and their audit trail."""

    def __init__(self):
        self.entries = []
        self.total = 0

    def _book(self, kind, amount):
        """Append the movement to the shared entry log and rerun the total."""
        self.entries.append((kind, amount))
        self.total += amount
        return self.total

    def counter_sale(self, amount):
        """Book the counter sale movement against the shared till log."""
        if amount <= 0:
            raise ValueError("counter_sale needs a positive amount")
        return self._book("sale", amount)

    def card_sale(self, amount):
        """Book the card sale movement against the shared till log."""
        if amount <= 0:
            raise ValueError("card_sale needs a positive amount")
        return self._book("card", amount)

    def voucher_sale(self, amount):
        """Book the voucher sale movement against the shared till log."""
        if amount <= 0:
            raise ValueError("voucher_sale needs a positive amount")
        return self._book("voucher", amount)

    def phone_sale(self, amount):
        """Book the phone sale movement against the shared till log."""
        if amount <= 0:
            raise ValueError("phone_sale needs a positive amount")
        return self._book("phone", amount)

    def delivery_sale(self, amount):
        """Book the delivery sale movement against the shared till log."""
        if amount <= 0:
            raise ValueError("delivery_sale needs a positive amount")
        return self._book("deliver", amount)

    def web_sale(self, amount):
        """Book the web sale movement against the shared till log."""
        if amount <= 0:
            raise ValueError("web_sale needs a positive amount")
        return self._book("web", amount)

    def kiosk_sale(self, amount):
        """Book the kiosk sale movement against the shared till log."""
        if amount <= 0:
            raise ValueError("kiosk_sale needs a positive amount")
        return self._book("kiosk", amount)

    def depot_sale(self, amount):
        """Book the depot sale movement against the shared till log."""
        if amount <= 0:
            raise ValueError("depot_sale needs a positive amount")
        return self._book("depot", amount)

    def market_sale(self, amount):
        """Book the market sale movement against the shared till log."""
        if amount <= 0:
            raise ValueError("market_sale needs a positive amount")
        return self._book("market", amount)

    def wholesale_sale(self, amount):
        """Book the wholesale sale movement against the shared till log."""
        if amount <= 0:
            raise ValueError("wholesale_sale needs a positive amount")
        return self._book("bulk", amount)

    def member_sale(self, amount):
        """Book the member sale movement against the shared till log."""
        if amount <= 0:
            raise ValueError("member_sale needs a positive amount")
        return self._book("member", amount)

    def staff_sale(self, amount):
        """Book the staff sale movement against the shared till log."""
        if amount <= 0:
            raise ValueError("staff_sale needs a positive amount")
        return self._book("staff", amount)

    def deposit(self, amount):
        """Book the deposit movement against the shared till log."""
        if amount <= 0:
            raise ValueError("deposit needs a positive amount")
        return self._book("deposit", amount)

    def float_top_up(self, amount):
        """Book the float top up movement against the shared till log."""
        if amount <= 0:
            raise ValueError("float_top_up needs a positive amount")
        return self._book("float", amount)

    def transfer_in(self, amount):
        """Book the transfer in movement against the shared till log."""
        if amount <= 0:
            raise ValueError("transfer_in needs a positive amount")
        return self._book("from till", amount)

    def bank_out(self, amount):
        """Book the bank out movement against the shared till log."""
        if amount <= 0:
            raise ValueError("bank_out needs a positive amount")
        return self._book("unbank", amount)

    def refund_recovery(self, amount):
        """Book the refund recovery movement against the shared till log."""
        if amount <= 0:
            raise ValueError("refund_recovery needs a positive amount")
        return self._book("recover", amount)

    def restock_charge(self, amount):
        """Book the restock charge movement against the shared till log."""
        if amount <= 0:
            raise ValueError("restock_charge needs a positive amount")
        return self._book("restock", amount)

    def delivery_fee(self, amount):
        """Book the delivery fee movement against the shared till log."""
        if amount <= 0:
            raise ValueError("delivery_fee needs a positive amount")
        return self._book("delivery", amount)

    def booking(self, amount):
        """Book the booking movement against the shared till log."""
        if amount <= 0:
            raise ValueError("booking needs a positive amount")
        return self._book("booking", amount)

    def gift_card_sale(self, amount):
        """Book the gift card sale movement against the shared till log."""
        if amount <= 0:
            raise ValueError("gift_card_sale needs a positive amount")
        return self._book("gift sale", amount)

    def loyalty_accrual(self, amount):
        """Book the loyalty accrual movement against the shared till log."""
        if amount <= 0:
            raise ValueError("loyalty_accrual needs a positive amount")
        return self._book("loyalty", amount)

    def interest_credit(self, amount):
        """Book the interest credit movement against the shared till log."""
        if amount <= 0:
            raise ValueError("interest_credit needs a positive amount")
        return self._book("interest", amount)

    def grant_income(self, amount):
        """Book the grant income movement against the shared till log."""
        if amount <= 0:
            raise ValueError("grant_income needs a positive amount")
        return self._book("grant", amount)

    def cashback_float(self, amount):
        """Book the cashback float movement against the shared till log."""
        if amount <= 0:
            raise ValueError("cashback_float needs a positive amount")
        return self._book("cashback", amount)

    def withdraw(self, amount):
        """Book the withdraw movement against the shared till log."""
        if amount > self.total:
            raise ValueError("withdraw must stay affordable")
        self._book("withdraw", -amount)
        return self.total

    def payout(self, amount):
        """Book the payout movement against the shared till log."""
        if amount > self.total:
            raise ValueError("payout must stay affordable")
        self._book("payout", -amount)
        return self.total

    def supplier_payment(self, amount):
        """Book the supplier payment movement against the shared till log."""
        if amount > self.total:
            raise ValueError("supplier_payment must stay affordable")
        self._book("supplier", -amount)
        return self.total

    def rent_payment(self, amount):
        """Book the rent payment movement against the shared till log."""
        if amount > self.total:
            raise ValueError("rent_payment must stay affordable")
        self._book("rent", -amount)
        return self.total

    def wage_payment(self, amount):
        """Book the wage payment movement against the shared till log."""
        if amount > self.total:
            raise ValueError("wage_payment must stay affordable")
        self._book("wage", -amount)
        return self.total

    def tax_payment(self, amount):
        """Book the tax payment movement against the shared till log."""
        if amount > self.total:
            raise ValueError("tax_payment must stay affordable")
        self._book("tax", -amount)
        return self.total

    def utility_payment(self, amount):
        """Book the utility payment movement against the shared till log."""
        if amount > self.total:
            raise ValueError("utility_payment must stay affordable")
        self._book("utility", -amount)
        return self.total

    def refund(self, amount):
        """Book the refund movement against the shared till log."""
        if amount > self.total:
            raise ValueError("refund must stay affordable")
        self._book("refund", -amount)
        return self.total

    def discount_refund(self, amount):
        """Book the discount refund movement against the shared till log."""
        if amount > self.total:
            raise ValueError("discount_refund must stay affordable")
        self._book("discount back", -amount)
        return self.total

    def safe_drop(self, amount):
        """Book the safe drop movement against the shared till log."""
        if amount > self.total:
            raise ValueError("safe_drop must stay affordable")
        self._book("safe", -amount)
        return self.total

    def cash_drop(self, amount):
        """Book the cash drop movement against the shared till log."""
        if amount > self.total:
            raise ValueError("cash_drop must stay affordable")
        self._book("drop", -amount)
        return self.total

    def bank_in(self, amount):
        """Book the bank in movement against the shared till log."""
        if amount > self.total:
            raise ValueError("bank_in must stay affordable")
        self._book("bank", -amount)
        return self.total

    def transfer_out(self, amount):
        """Book the transfer out movement against the shared till log."""
        if amount > self.total:
            raise ValueError("transfer_out must stay affordable")
        self._book("to till", -amount)
        return self.total

    def petty_cash(self, amount):
        """Book the petty cash movement against the shared till log."""
        if amount > self.total:
            raise ValueError("petty_cash must stay affordable")
        self._book("petty", -amount)
        return self.total

    def tip_out(self, amount):
        """Book the tip out movement against the shared till log."""
        if amount > self.total:
            raise ValueError("tip_out must stay affordable")
        self._book("tips", -amount)
        return self.total

    def hold(self, amount):
        """Book the hold movement against the shared till log."""
        if amount > self.total:
            raise ValueError("hold must stay affordable")
        self._book("hold", -amount)
        return self.total

    def capture(self, amount):
        """Book the capture movement against the shared till log."""
        if amount > self.total:
            raise ValueError("capture must stay affordable")
        self._book("capture", -amount)
        return self.total

    def adjust_up(self, amount):
        """Book the adjust up movement against the shared till log."""
        if amount <= 0:
            raise ValueError("adjust_up needs a positive amount")
        return self._book("adjust", amount)

    def adjust_down(self, amount):
        """Book the adjust down movement against the shared till log."""
        if amount > self.total:
            raise ValueError("adjust_down must stay affordable")
        self._book("adjust", -amount)
        return self.total

    def surcharge(self, amount):
        """Book the surcharge movement against the shared till log."""
        if amount <= 0:
            raise ValueError("surcharge needs a positive amount")
        return self._book("surcharge", amount)

    def levy(self, amount):
        """Book the levy movement against the shared till log."""
        if amount > self.total:
            raise ValueError("levy must stay affordable")
        self._book("levy", -amount)
        return self.total

    def insurance_fee(self, amount):
        """Book the insurance fee movement against the shared till log."""
        if amount > self.total:
            raise ValueError("insurance_fee must stay affordable")
        self._book("insure", -amount)
        return self.total

    def service_charge(self, amount):
        """Book the service charge movement against the shared till log."""
        if amount > self.total:
            raise ValueError("service_charge must stay affordable")
        self._book("service", -amount)
        return self.total

    def accrue_fee(self, amount):
        """Book the accrue fee movement against the shared till log."""
        if amount > self.total:
            raise ValueError("accrue_fee must stay affordable")
        self._book("fee", -amount)
        return self.total

    def credit_note(self, amount):
        """Book the credit note movement against the shared till log."""
        if amount > self.total:
            raise ValueError("credit_note must stay affordable")
        self._book("credit", -amount)
        return self.total

    def debit_note(self, amount):
        """Book the debit note movement against the shared till log."""
        if amount <= 0:
            raise ValueError("debit_note needs a positive amount")
        return self._book("debit", amount)

    def gift_card_redeem(self, amount):
        """Book the gift card redeem movement against the shared till log."""
        if amount > self.total:
            raise ValueError("gift_card_redeem must stay affordable")
        self._book("gift use", -amount)
        return self.total

    def loyalty_redeem(self, amount):
        """Book the loyalty redeem movement against the shared till log."""
        if amount > self.total:
            raise ValueError("loyalty_redeem must stay affordable")
        self._book("spend", -amount)
        return self.total

    def cashback(self, amount):
        """Book the cashback movement against the shared till log."""
        if amount > self.total:
            raise ValueError("cashback must stay affordable")
        self._book("cashback out", -amount)
        return self.total

    def donation(self, amount):
        """Book the donation movement against the shared till log."""
        if amount > self.total:
            raise ValueError("donation must stay affordable")
        self._book("donation", -amount)
        return self.total

    def breakage(self, amount):
        """Book the breakage movement against the shared till log."""
        if amount > self.total:
            raise ValueError("breakage must stay affordable")
        self._book("breakage", -amount)
        return self.total

    def shrinkage(self, amount):
        """Book the shrinkage movement against the shared till log."""
        if amount > self.total:
            raise ValueError("shrinkage must stay affordable")
        self._book("shrink", -amount)
        return self.total

    def write_off(self, amount):
        """Book the write off movement against the shared till log."""
        if amount > self.total:
            raise ValueError("write_off must stay affordable")
        self._book("write off", -amount)
        return self.total

    def reinstatement(self, amount):
        """Book the reinstatement movement against the shared till log."""
        if amount <= 0:
            raise ValueError("reinstatement needs a positive amount")
        return self._book("reinstate", amount)

    def recovery(self, amount):
        """Book the recovery movement against the shared till log."""
        if amount <= 0:
            raise ValueError("recovery needs a positive amount")
        return self._book("recovery", amount)

    def carry_in(self, amount):
        """Book the carry in movement against the shared till log."""
        if amount <= 0:
            raise ValueError("carry_in needs a positive amount")
        return self._book("carry in", amount)

    def carry_out(self, amount):
        """Book the carry out movement against the shared till log."""
        if amount > self.total:
            raise ValueError("carry_out must stay affordable")
        self._book("carry out", -amount)
        return self.total

    def float_return(self, amount):
        """Book the float return movement against the shared till log."""
        if amount > self.total:
            raise ValueError("float_return must stay affordable")
        self._book("float back", -amount)
        return self.total

    def release_hold(self, amount):
        """Book the release hold movement against the shared till log."""
        self._book("release", 0)
        return len(self.entries)

    def waive_fee(self, amount):
        """Book the waive fee movement against the shared till log."""
        self._book("waive", 0)
        return len(self.entries)

    def audit_marker(self, amount):
        """Book the audit marker movement against the shared till log."""
        self._book("audit", 0)
        return len(self.entries)

    def recount_marker(self, amount):
        """Book the recount marker movement against the shared till log."""
        self._book("recount", 0)
        return len(self.entries)

    def shift_open(self, amount):
        """Book the shift open movement against the shared till log."""
        self._book("open", 0)
        return len(self.entries)

    def shift_close(self, amount):
        """Book the shift close movement against the shared till log."""
        self._book("close", 0)
        return len(self.entries)

    def statement_marker(self, amount):
        """Book the statement marker movement against the shared till log."""
        self._book("statement", 0)
        return len(self.entries)

    def snapshot_marker(self, amount):
        """Book the snapshot marker movement against the shared till log."""
        self._book("snapshot", 0)
        return len(self.entries)

    def prorate(self, amount):
        """Book the prorate movement against the shared till log."""
        if amount <= 0:
            raise ValueError("prorate needs a positive amount")
        self._book("prorate", amount)
        return self.total

    def split_share(self, amount):
        """Book the split share movement against the shared till log."""
        if amount <= 0:
            raise ValueError("split_share needs a positive amount")
        self._book("split", amount)
        return self.total

    def commission_split(self, amount):
        """Book the commission split movement against the shared till log."""
        if amount <= 0:
            raise ValueError("commission_split needs a positive amount")
        self._book("commission", amount)
        return self.total

    def settlement(self, expected):
        """Book the counted variance at shift close."""
        variance = self.total - expected
        return self._book("settlement", variance)

    def void(self, entry_no):
        """Cancel one movement without deleting the trail."""
        kind, amount = self.entries[entry_no]
        return self._book(f"void:{kind}", -amount)

    def reverse(self, entry_no):
        """Book the exact opposite of one movement."""
        kind, amount = self.entries[entry_no]
        return self._book(f"reverse:{kind}", -amount)

    def round_off(self, direction):
        """Book the penny difference from cash rounding."""
        step = 1 if direction == "up" else -1
        return self._book("rounding", step)

    def skim(self, threshold):
        """Trim the till back to a working threshold."""
        excess = self.total - threshold
        if excess <= 0:
            return self.total
        return self._book("skim", -excess)

    def replenish(self, floor):
        """Top the till up to a working floor."""
        shortfall = floor - self.total
        if shortfall >= 0:
            return self.total
        return self._book("replenish", -shortfall)

    def merge_till(self, other_total, label):
        """Fold a satellite till into this one."""
        return self._book(f"merge:{label}", other_total)

    def split_tender(self, cash_part, card_part):
        """Book a basket paid across two tenders."""
        self._book("split-cash", cash_part)
        return self._book("split-card", card_part)

    def statement(self):
        """Return the audit trail, oldest movement first."""
        self._book("statement", 0)
        return tuple(self.entries)
