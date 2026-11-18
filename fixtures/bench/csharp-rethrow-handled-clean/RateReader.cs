using System.IO;
using Microsoft.Extensions.Logging;

namespace Warehouse.Intake
{
    public sealed class RateReader
    {
        private readonly ILogger _logger;
        private bool _failed;
        public RateReader(ILogger logger)
        {
            _logger = logger;
        }

        public decimal Read(string path)
        {
            try
            {
                return decimal.Parse(File.ReadAllText(path));
            }
            catch (IOException ex)
            {
                _logger.LogWarning(ex.Message);
                _failed = true;
                return 0.0m;
            }
        }
    }
}
