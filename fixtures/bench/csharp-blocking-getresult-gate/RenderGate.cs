using System.Threading.Tasks;

namespace Warehouse.Rendering
{
    public sealed class RenderGate
    {
        public string Render()
        {
            var doc = RenderAsync().GetAwaiter().GetResult();
            return doc;
        }

        private static async Task<string> RenderAsync()
        {
            await Task.Delay(1);
            return "doc";
        }
    }
}
