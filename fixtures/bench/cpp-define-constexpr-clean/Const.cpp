constexpr int kTimeoutMs = 5000;

#define LOG_FN(name) log_enter(name)

int ConnectTimeout(void)
{
    return kTimeoutMs;
}
