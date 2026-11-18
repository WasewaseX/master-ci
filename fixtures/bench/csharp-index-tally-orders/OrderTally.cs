using System.Collections.Generic;

namespace Warehouse.Orders
{
    public sealed class OrderTally
    {
        public int Total(IReadOnlyList<int> orders)
        {
            var sum = 0;
            for (int i = 0; i < orders.Count; i++)
            {
                sum += orders[i];
            }
            return sum;
        }
    }
}
