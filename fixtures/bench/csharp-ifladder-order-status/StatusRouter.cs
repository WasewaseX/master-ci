namespace Warehouse.Orders
{
    public sealed class StatusRouter
    {
        public string Next(string status)
        {
            if (status == "Pending") { return "Pay"; }
            else if (status == "Paid") { return "Pack"; }
            else if (status == "Packed") { return "Ship"; }
            else if (status == "Shipped") { return "Track"; }
            else if (status == "Delivered") { return "Archive"; }
            return "Unknown";
        }
    }
}
