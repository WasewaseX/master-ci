namespace Warehouse.Shipping
{
    public sealed class RateTable
    {
        public decimal Rate(string tier)
        {
            if (tier == "Ground")
            {
                return 0.5m;
            }
            else if (tier == "Air")
            {
                return 1.2m;
            }
            else if (tier == "Sea")
            {
                return 0.8m;
            }
            else if (tier == "Rail")
            {
                return 0.65m;
            }
            return 1.0m;
        }
    }
}
