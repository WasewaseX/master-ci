using System.Diagnostics;

namespace Warehouse.Inventory
{
    public sealed class StockRepository
    {
        public int Count(string warehouse)
        {
            Trace.WriteLine("stock query for " + warehouse);
            return 42;
        }
    }
}
