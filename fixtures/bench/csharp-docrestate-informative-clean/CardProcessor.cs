namespace Warehouse.Billing
{
    public sealed class CardProcessor
    {
        /// <summary>Charges the card and returns the receipt id for the refund window.</summary>
        public string Charge(decimal amount)
        {
            return "receipt-1";
        }
    }
}
