using System.Collections.Generic;
using System.Linq;

namespace Warehouse.Picks
{
    public sealed class PickVerifier
    {
        public bool HasPicks(IEnumerable<string> picked)
        {
            return picked.Count() > 0;
        }
    }
}
