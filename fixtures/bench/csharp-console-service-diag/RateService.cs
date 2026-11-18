using System.Diagnostics;

namespace Warehouse.Pricing
{
    public sealed class RateService
    {
        public decimal RateFor(string sku)
        {
            Debug.WriteLine("rate lookup for " + sku);
            return 1.0m;
        }
    }
}
