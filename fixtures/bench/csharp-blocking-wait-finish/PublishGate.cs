using System.Threading.Tasks;

namespace Warehouse.Publishing
{
    public sealed class PublishGate
    {
        public void Publish()
        {
            var publish = PublishAsync();
            publish.Wait();
        }

        private static async Task PublishAsync()
        {
            await Task.Delay(1);
        }
    }
}
