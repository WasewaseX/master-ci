using System.Threading.Tasks;

namespace Portal.Dashboard
{
    public class DashboardViewModel
    {
        private readonly IFeedClient _feed;

        public DashboardViewModel(IFeedClient feed)
        {
            _feed = feed;
        }

        public async void RefreshPanels()
        {
            await _feed.ReloadAsync();
        }

        public async void StartBackgroundSync()
        {
            await _feed.SubscribeAsync();
        }
    }
}
