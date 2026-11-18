using System.Threading.Tasks;

namespace Warehouse.Startup
{
    public sealed class WarmupGate
    {
        public string Warm()
        {
            var warmup = WarmAsync();
            warmup.Wait();
            var ver = VersionAsync().Result;
            return ver;
        }

        private static async Task WarmAsync()
        {
            await Task.Delay(1);
        }

        private static async Task<string> VersionAsync()
        {
            await Task.Delay(1);
            return "v2";
        }
    }
}
