#define OVERSAMPLE_FACTOR 4

int OutputRate(int input)
{
    return input * OVERSAMPLE_FACTOR;
}
