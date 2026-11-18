using Microsoft.Extensions.Logging;

namespace Warehouse.Pricing
{
    public sealed class RateService
    {
        private readonly ILogger _logger;

        public RateService(ILogger logger)
        {
            _logger = logger;
        }

        public decimal RateFor(string sku)
        {
            _logger.LogInformation("rate lookup for {Sku}", sku);
            return 1.0m;
        }
    }
}
