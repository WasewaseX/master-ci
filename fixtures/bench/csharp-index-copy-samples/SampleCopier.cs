namespace Warehouse.Sensors
{
    public sealed class SampleCopier
    {
        public double[] Copy(double[] samples)
        {
            var target = new double[samples.Length];
            for (var i = 0; i < samples.Length; i++)
            {
                target[i] = samples[i] * 2;
            }
            return target;
        }
    }
}
