namespace Warehouse.Global
{
    public sealed class LocaleMap
    {
        public string Currency(string locale)
        {
            if (locale == "en-US")
            {
                return "USD";
            }
            else if (locale == "de-DE")
            {
                return "EUR";
            }
            else if (locale == "ja-JP")
            {
                return "JPY";
            }
            else if (locale == "en-GB")
            {
                return "GBP";
            }
            return "USD";
        }
    }
}
