namespace Warehouse.Shipping
{
    public sealed class RateTable
    {
        public decimal Rate(string tier)
        {
            switch (tier)
            {
                case "Ground":
                    return 0.5m;
                case "Air":
                    return 1.2m;
                case "Sea":
                    return 0.8m;
                default:
                    return 1.0m;
            }
        }
    }
}
