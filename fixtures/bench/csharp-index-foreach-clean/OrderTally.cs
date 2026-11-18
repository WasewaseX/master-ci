using System.Collections.Generic;

namespace Warehouse.Orders
{
    public sealed class OrderTally
    {
        public int Total(IReadOnlyList<int> orders)
        {
            var sum = 0;
            foreach (var order in orders)
            {
                sum += order;
            }
            return sum;
        }
    }
}
