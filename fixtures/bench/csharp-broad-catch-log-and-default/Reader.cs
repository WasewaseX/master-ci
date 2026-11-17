namespace Ingest
{
    public class ConfigReader
    {
        private readonly ISettingsFile _file;
        private readonly ILogger _logger;

        public ConfigReader(ISettingsFile file, ILogger logger)
        {
            _file = file;
            _logger = logger;
        }

        public string Read()
        {
            try
            {
                return _file.Load();
            }
            catch (Exception ex)
            {
                _logger.LogInformation("settings load failed, defaults in use");
                return null;
            }
        }
    }
}
