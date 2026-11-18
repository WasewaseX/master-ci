using System.Threading.Tasks;

namespace Warehouse.Intake
{
    public sealed class SummaryGate
    {
        public int Total()
        {
            var total = SumAsync().Result;
            return total;
        }

        private static async Task<int> SumAsync()
        {
            await Task.Delay(1);
            return 5;
        }
    }
}
