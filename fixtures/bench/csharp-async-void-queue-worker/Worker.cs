using System.Threading.Tasks;

namespace Pipeline.Workers
{
    public class QueueWorker
    {
        private readonly IMessagePump _pump;

        public QueueWorker(IMessagePump pump)
        {
            _pump = pump;
        }

        public async void ProcessQueue()
        {
            while (await _pump.MoveNextAsync())
            {
                await _pump.DispatchAsync();
            }
        }
    }
}
