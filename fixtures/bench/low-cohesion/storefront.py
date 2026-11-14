"""Counter operations hub (fixture: two disjoint responsibilities)."""


class CounterHub:
    def __init__(self):
        self.balance = 0
        self.outbox = []

    def deposit(self, amount):
        self.balance += amount

    def withdraw(self, amount):
        self.balance -= amount

    def broadcast(self, message):
        self.outbox.append(message)

    def drain_outbox(self):
        sent = len(self.outbox)
        self.outbox.clear()
        return sent
