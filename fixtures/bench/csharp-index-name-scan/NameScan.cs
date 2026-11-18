using System.Collections.Generic;

namespace Warehouse.Directories
{
    public sealed class NameScan
    {
        public int FirstBlank(IReadOnlyList<string> names)
        {
            for (int i = 0; i < names.Count; i++)
            {
                if (names[i].Length == 0)
                {
                    return i;
                }
            }
            return -1;
        }
    }
}
