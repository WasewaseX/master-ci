namespace Audit
{
    public class ChangeAuditor
    {
        private readonly ILogger _logger;
        private readonly IEventStore _store;

        public ChangeAuditor(ILogger logger, IEventStore store)
        {
            _logger = logger;
            _store = store;
        }

        public void Record(Change change)
        {
            try
            {
                _store.Append(change);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "append deferred");
                _store.QueueRetry(change);
            }
        }
    }
}
