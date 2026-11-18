using Microsoft.Win32;

namespace Warehouse.Platform
{
    public sealed class RegistryEntry
    {
        public string Read(string key)
        {
            return Registry.GetValue(key)!.ToString();
        }
    }
}
