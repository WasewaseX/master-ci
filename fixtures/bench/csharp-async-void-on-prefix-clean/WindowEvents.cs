using System.Threading.Tasks;

namespace Portal.Shell
{
    public class WindowEvents
    {
        private readonly IStateStore _store;

        public WindowEvents(IStateStore store)
        {
            _store = store;
        }

        public async void OnMessageReceived(Envelope envelope)
        {
            await _store.ApplyAsync(envelope);
        }

        public async void OnFocusGained()
        {
            await _store.RefreshAsync();
        }
    }
}
