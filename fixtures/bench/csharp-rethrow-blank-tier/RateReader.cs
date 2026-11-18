using System;

namespace Warehouse.Intake
{
    public sealed class RateReader
    {
        public decimal ReadRate()
        {
            try
            {
                return decimal.Parse("1.5");
            }
            catch
            {
                throw;
            }
        }
    }
}
