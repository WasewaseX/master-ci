using System.Threading.Tasks;

namespace Warehouse.Intake
{
    public sealed class SummaryGate
    {
        public async Task<int> TotalAsync()
        {
            await Task.Delay(1);
            return 5;
        }
    }
}
