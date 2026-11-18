using System;

namespace Warehouse.Intake
{
    public sealed class SkuParser
    {
        public int ParseTier(string raw)
        {
            try
            {
                return int.Parse(raw);
            }
            catch (FormatException)
            {
                throw;
            }
        }
    }
}
