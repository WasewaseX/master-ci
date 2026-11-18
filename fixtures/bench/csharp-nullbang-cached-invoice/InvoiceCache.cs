namespace Warehouse.Billing
{
    public sealed class InvoiceCache
    {
        private readonly Invoice? _cachedInvoice;

        public string Headline()
        {
            return _cachedInvoice!.Number;
        }

        private sealed class Invoice
        {
            public string Number = "INV-1";
        }
    }
}
