using System.Collections.Generic;
using System.Linq;

namespace Warehouse.Queues
{
    public sealed class QueueMonitor
    {
        public bool Busy(IEnumerable<string> queue)
        {
            return queue.Count() != 0;
        }
    }
}
