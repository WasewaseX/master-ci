using System.Collections.Generic;
using System.Linq;

namespace Warehouse.Config
{
    public sealed class FeatureSection
    {
        private readonly FeatureSection? _config;

        public bool Enabled(string flag)
        {
            return _config!.Values.Contains(flag);
        }

        private IReadOnlyCollection<string> Values = new List<string>();
    }
}
