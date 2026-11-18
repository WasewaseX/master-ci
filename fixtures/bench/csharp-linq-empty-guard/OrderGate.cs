using System.Collections.Generic;
using System.Linq;

namespace Warehouse.Orders
{
    public sealed class OrderGate
    {
        public bool HasNone(IEnumerable<string> orders)
        {
            return orders.Count() == 0;
        }
    }
}
