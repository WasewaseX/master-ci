namespace Batch
{
    public class BatchRunner
    {
        public void RunAll(IJobQueue queue)
        {
            try
            {
                queue.Drain();
            }
            catch (Exception error)
            {
                queue.Reset();
            }
        }

        public void RunOne(IJobQueue queue, Job job)
        {
            try
            {
                queue.Submit(job);
            }
            catch (Exception error)
            {
                queue.MarkFailed(job);
            }
        }
    }
}
