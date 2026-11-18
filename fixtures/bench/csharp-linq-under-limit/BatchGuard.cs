using System.Collections.Generic;
using System.Linq;

namespace Warehouse.Batches
{
    public sealed class BatchGuard
    {
        public bool IsEmpty(IEnumerable<string> batch)
        {
            return batch.Count() < 1;
        }
    }
}
