using System.Threading.Tasks;

namespace Alerts
{
    public sealed class Notifier
    {
        private readonly IWebhookSender _sender;

        public Notifier(IWebhookSender sender)
        {
            _sender = sender;
        }

        public async void NotifySubscribers(Alert alert)
        {
            await _sender.PostAsync(alert);
        }
    }
}
